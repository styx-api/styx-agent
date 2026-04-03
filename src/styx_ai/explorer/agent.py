"""Explorer agent: navigates package source code to extract CLI information."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import litellm

from styx_ai.tools.filesystem import TOOL_DEFINITIONS, execute_tool

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini/gemini-2.5-flash"
MAX_TURNS = 40


SYSTEM_PROMPT = """\
You are a source code researcher. You explore source repositories and produce \
thorough reports on command-line tool interfaces.

You will be given a tool name. Locate its entry point in the repository, then \
extract its complete interface. A tool's entry point might be a script (shell, \
Python, etc.) or a compiled source file with a main function. If a file with the \
tool's exact name exists, start there. A wrapper script and the binary it calls \
are DIFFERENT tools with separate interfaces.

## What to extract

For each tool, produce a report covering:

### Inputs

Every input the tool accepts. For each one, document:

- **Name** — identifier from the source (variable name, option name)
- **Description** — help text, quoted verbatim from source
- **Type** — as observed in source (e.g. `atof` → float, `atoi` → int, \
`Option<bool>` → boolean). Show the code, let downstream agents interpret.
- **Cardinality** — how many values: one, a fixed count (e.g. 3 for x/y/z), \
or unbounded list
- **Optionality** — required or optional, with default value if any
- **Syntax** — how the user provides it: flag (`-f`, `--flag`), positional \
(by index), or other
- **Constraints** — value ranges, allowed choices, mutual exclusions, \
dependencies on other inputs
- **Source snippet** — the code where this input is defined

### Outputs

Every file or artifact the tool produces. For each one:

- **Path pattern** — how the output path is constructed from inputs
- **Condition** — which inputs control whether this output is produced
- **Source snippet** — the actual write/save call (not guessed from conventions)

If the tool delegates to sub-binaries that produce outputs, trace into their \
source to find the actual write calls. Grep for file-writing functions \
(`save_volume`, `save`, `write`, `fopen`, etc.) and read the surrounding code.

### Delegated tools

If this tool invokes other tools that produce user-visible outputs, list them \
with how they are called. Only include tools whose outputs become part of this \
tool's output — not internal utilities.

## Report format

Use these sections in this order. Write the report directly as markdown \
(do NOT wrap it in a code block):

- `# <tool_name>` — tool name as heading, one-line description below
- `## Invocation` — usage pattern
- `## Parsing approach` — parser used, source files, confidence level
- `## Inputs` — for each input: name, description, type, cardinality, \
optionality, syntax, constraints, source snippet
- `## Constraints` — mutual exclusions, dependencies between inputs \
(omit if none)
- `## Outputs` — for each output: path pattern, condition, source snippet
- `## Source files examined` — files you read
- `## Uncertainties` — anything you could not determine confidently

## Source references

Annotate every source snippet with file path and line numbers:

<!-- source: path/to/file.cpp:55-57 -->
```cpp
code here
```

## How to work

- Read the argument parsing code — source code is the authority, not help text.
- Quote help text verbatim when available.
- Read ALL of each relevant source file. Page through large files with \
offset/limit or use read_tail.
- Find output paths by grepping for file-writing calls in the source, then \
reading the surrounding context. Every output in your report must have a \
source snippet showing the actual write call.
- When a tool delegates to sub-binaries, read those sources too to find their \
outputs. Cover all code paths (modes, branches, conditional logic).
- Mark uncertainties explicitly rather than guessing. But before reporting \
an uncertainty, check if you can resolve it by reading another file in the \
repo (e.g. a header file, a shared library source). Only report uncertainties \
you genuinely cannot resolve from the available source code.
"""


FSL_STRATEGY = """\

## Package-specific: FSL

FSL argument parsing patterns:

**Option<T> / OptionParser** — `#include "utils/options.h"`:
```cpp
Option<float> threshold(string("-f"), 0.5,
    string("fractional intensity threshold (0->1)"),
    false, requires_argument);
```
Constructor: `Option<T>(keys, default, help_text, compulsory, arg_flag)`
- `no_argument` = boolean, `requires_argument` = 1 value, `requires_N_arguments` = N values

**Manual if/else** — older tools:
```cpp
if (arg == "-bins") { no_bins = atoi(argv[n+1]); n+=2; continue; }
```

**Shell wrappers** — many tools have a shell script wrapping a C++ binary. \
Parse via `case`/`esac`, `getopts`, or `shift` loops.

Look in: `*.cc`, `*.cpp`, shell scripts (no extension or `.sh`), `utils/options.h`.
"""

STRATEGIES: dict[str, str] = {
    "fsl": FSL_STRATEGY,
}


async def _run_agent(
    system_prompt: str,
    user_message: str,
    repo_root: str,
    model: str,
    max_turns: int = MAX_TURNS,
) -> str:
    """Run an LLM agent loop with filesystem tools."""
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    for turn in range(max_turns):
        logger.info(f"Agent turn {turn + 1}/{max_turns}")

        for attempt in range(5):
            try:
                response = await litellm.acompletion(
                    model=model,
                    messages=messages,
                    tools=TOOL_DEFINITIONS,
                    max_tokens=16384,
                )
                break
            except litellm.exceptions.RateLimitError:
                wait = min(2 ** attempt * 10, 60)
                logger.warning(f"Rate limited, waiting {wait}s (attempt {attempt + 1}/5)")
                await asyncio.sleep(wait)
                if attempt == 4:
                    raise

        if not response.choices:
            logger.warning("Empty response from API, retrying...")
            continue

        choice = response.choices[0]
        message = choice.message

        if not message.tool_calls:
            return message.content or ""

        messages.append(message.model_dump())

        for tool_call in message.tool_calls:
            fn = tool_call.function
            args = json.loads(fn.arguments)
            logger.info(f"  Tool: {fn.name}({fn.arguments})")
            result = execute_tool(fn.name, args, repo_root)
            logger.debug(f"  Result: {result[:200]}...")

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                }
            )

    raise RuntimeError(f"Agent exceeded {max_turns} turns without producing a result")


async def explore(
    tool_name: str,
    repo_path: str | Path,
    package: str = "fsl",
    model: str = DEFAULT_MODEL,
) -> str:
    """Run the Explorer agent to extract CLI information from source code.

    Args:
        tool_name: Name of the tool command to analyze (e.g. 'bet', 'bet2').
        repo_path: Path to the cloned source repository.
        package: Package identifier for strategy selection.
        model: LLM model to use.

    Returns:
        Markdown report describing the tool's CLI interface.
    """
    repo_root = str(Path(repo_path).resolve())
    strategy = STRATEGIES.get(package, "")
    system_prompt = SYSTEM_PROMPT + strategy

    user_msg = (
        f"Analyze the tool '{tool_name}' in this repository. "
        f"Produce a complete report of its interface."
    )

    return await _run_agent(
        system_prompt=system_prompt,
        user_message=user_msg,
        repo_root=repo_root,
        model=model,
    )
