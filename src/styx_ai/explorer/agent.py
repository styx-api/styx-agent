"""Explorer agents: extract CLI interface and output information from source code.

Two agents work in sequence:
1. Interface agent — extracts inputs, constraints, parsing approach
2. Output agent — traces output file generation through the source
"""

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


# ---------------------------------------------------------------------------
# Interface agent
# ---------------------------------------------------------------------------

INTERFACE_PROMPT = """\
You are a source code researcher. You explore source repositories and extract \
command-line tool interfaces.

You will be given a tool name. Locate its entry point in the repository, then \
extract its complete input interface. A tool's entry point might be a script \
(shell, Python, etc.) or a compiled source file with a main function. If a file \
with the tool's exact name exists, start there. A wrapper script and the binary \
it calls are DIFFERENT tools with separate interfaces.

## What to extract

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
- **Constraints** — value ranges, allowed choices, dependencies on other inputs
- **Source snippet** — the code where this input is defined

### Constraints

Mutual exclusions and dependencies between inputs.

## Report format

Write the report directly as markdown (do NOT wrap it in a code block):

- `# <tool_name>` — tool name as heading, one-line description below
- `## Invocation` — usage pattern
- `## Parsing approach` — parser used, source files, confidence level
- `## Inputs` — for each input: all fields listed above
- `## Constraints` — mutual exclusions, dependencies (omit if none)
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
- Mark uncertainties explicitly rather than guessing.
"""


# ---------------------------------------------------------------------------
# Output agent
# ---------------------------------------------------------------------------

OUTPUT_PROMPT = """\
You are a source code researcher. You trace output file generation in source \
repositories.

You will be given a tool name and an interface report from a previous analysis. \
Your job is to find every file or artifact the tool produces by tracing through \
the actual source code.

## What to extract

For every output the tool produces, document:

- **Path pattern** — the COMPLETE filename(s) produced, including extensions. \
Trace the write call down to where the actual filename string is constructed. \
If a library function adds extensions (e.g. a macro appends `.HEAD`/`.BRIK`), \
follow it into the library source to find out what files actually appear on \
disk. "Writes a dataset at prefix" is NOT sufficient — the downstream agent \
needs exact filenames like `<prefix>+orig.HEAD` and `<prefix>+orig.BRIK`.
- **Condition** — which inputs control whether this output is produced
- **Source snippet** — the actual write/save call, AND the code that constructs \
the filename

## How to work

1. Start by grepping for file-writing calls across the source tree: \
`save_volume`, `save`, `write`, `fopen`, `DSET_write`, `fprintf` to files, \
shell redirects, etc. Filter to files relevant to this tool.
2. For each write call, trace the filename construction:
   - What path/filename string is passed to the write function?
   - Is it a complete filename or does the write function add extensions?
   - If the write function is a macro or wrapper, grep for its definition \
across the entire source tree. Search without a glob filter so you find \
definitions in headers (*.h), source files (*.c, *.cpp), and anywhere else. \
Don't limit searches to the tool's own source file.
3. For each write call, determine what condition controls whether it executes.
4. If the tool delegates to sub-binaries (you can see this from the interface \
report or by reading the source), trace into those sources too.
5. Cover ALL code paths — different modes, conditional branches, separate \
main functions.

## Report format

Write the report directly as markdown (do NOT wrap it in a code block):

- `# <tool_name> — Outputs`
- `## Outputs` — for each output: path pattern, condition, source snippet
- `## Source files examined` — files you read
- `## Uncertainties` — anything you investigated but could not resolve

Before reporting an uncertainty, try to resolve it: grep for the function or \
macro in question, read header files, follow the call chain. Only report an \
uncertainty after you have actually looked and could not find the answer in \
the repository.

## Source references

Annotate every source snippet with file path and line numbers:

<!-- source: path/to/file.cpp:55-57 -->
```cpp
code here
```
"""


# ---------------------------------------------------------------------------
# Package-specific strategies
# ---------------------------------------------------------------------------

FSL_INTERFACE_STRATEGY = """\

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

FSL_OUTPUT_STRATEGY = """\

## Package-specific: FSL

FSL tools typically write outputs via `save_volume`, `save_volume4D`, or \
mesh `.save()` calls. Output filenames are usually constructed by appending \
suffixes to a base output name (e.g. `out + "_mask"`).

Shell wrapper scripts may produce additional outputs via `fslmaths`, `immv`, \
or by calling sub-binaries like `betsurf`. Trace these calls to find the \
actual output paths.
"""

STRATEGIES: dict[str, dict[str, str]] = {
    "fsl": {
        "interface": FSL_INTERFACE_STRATEGY,
        "output": FSL_OUTPUT_STRATEGY,
    },
}


# ---------------------------------------------------------------------------
# Agent runner
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def explore_interface(
    tool_name: str,
    repo_path: str | Path,
    package: str = "fsl",
    model: str = DEFAULT_MODEL,
) -> str:
    """Extract CLI input interface from source code.

    Returns:
        Markdown report of inputs, constraints, and parsing approach.
    """
    repo_root = str(Path(repo_path).resolve())
    strategies = STRATEGIES.get(package, {})
    system_prompt = INTERFACE_PROMPT + strategies.get("interface", "")

    return await _run_agent(
        system_prompt=system_prompt,
        user_message=(
            f"Analyze the tool '{tool_name}' in this repository. "
            f"Produce a complete report of its input interface."
        ),
        repo_root=repo_root,
        model=model,
    )


async def explore_outputs(
    tool_name: str,
    repo_path: str | Path,
    interface_report: str,
    package: str = "fsl",
    model: str = DEFAULT_MODEL,
) -> str:
    """Trace output file generation from source code.

    Args:
        interface_report: The interface report from explore_interface,
            provided as context so the output agent knows which inputs
            control output generation.

    Returns:
        Markdown report of output files with path patterns and conditions.
    """
    repo_root = str(Path(repo_path).resolve())
    strategies = STRATEGIES.get(package, {})
    system_prompt = OUTPUT_PROMPT + strategies.get("output", "")

    return await _run_agent(
        system_prompt=system_prompt,
        user_message=(
            f"Trace all output files produced by the tool '{tool_name}' "
            f"in this repository.\n\n"
            f"Here is the interface report from a previous analysis — use it "
            f"to understand which inputs exist and how the tool is structured:\n\n"
            f"---\n\n{interface_report}"
        ),
        repo_root=repo_root,
        model=model,
    )


async def explore(
    tool_name: str,
    repo_path: str | Path,
    package: str = "fsl",
    model: str = DEFAULT_MODEL,
) -> str:
    """Run both Explorer agents and combine their reports.

    Returns:
        Combined markdown report (interface + outputs).
    """
    interface_report = await explore_interface(
        tool_name, repo_path, package=package, model=model,
    )
    logger.info("Interface report complete, starting output tracing...")

    output_report = await explore_outputs(
        tool_name, repo_path, interface_report, package=package, model=model,
    )

    # Combine: use interface report as base, append outputs section
    return f"{interface_report}\n\n{output_report}"
