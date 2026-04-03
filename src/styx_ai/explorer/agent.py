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
You are a source code researcher. Your job is to explore a software package's \
source repository and produce a thorough report on a specific CLI tool's interface.

You have filesystem tools (list_directory, read_file, grep, find_files) to \
navigate the repository.

## Step 1: Locate the entry point

You will be given a tool command name. First, find the EXACT source file that \
implements this tool's CLI:

1. Check if a file with the tool's exact name exists (e.g. a script named `bet` \
with no extension). If it does, read it — it may be a shell script, Python script, \
or similar. A script wrapper and a compiled binary with a similar name are \
DIFFERENT tools.
2. If no exact-name file exists, search for source files where the tool is built \
(e.g. a .cpp file with main() that compiles to a binary with that name).
3. Check build files (Makefile, CMakeLists.txt) if the mapping from source to \
binary name is unclear.

IMPORTANT: The repository may contain multiple tools. Focus ONLY on the specific \
tool you are asked about. Do not conflate a wrapper script with the binary it calls \
— they have separate parameter sets. However, you MUST still trace output file \
generation into sub-binaries that the tool calls, because the tool's outputs include \
whatever its sub-binaries produce.

## Step 2: Extract the CLI interface

### What to capture

#### Tool identity
- Binary/script name, one-line description
- Invocation pattern (e.g. `tool [options] <input> <output>` or `tool <subcommand> ...`)

#### Argument parsing approach
- What parsing pattern is used (e.g. library like getopt/argparse/Option<T>, \
manual if/else, shell case/esac, getopts)
- How much confidence the extraction warrants (declarative parser = high, \
manual parsing = lower)

#### Every parameter
For each CLI parameter, document:
- Flag string(s) (e.g. `-f`, `--fractional-intensity`, or positional)
- Data type as parsed in source (e.g. `atof` = float, `atoi` = int, string, bool flag)
- Default value if any
- Help text / description (quote verbatim from source)
- Constraints: min/max, allowed values, required vs optional
- Number of values consumed (e.g. takes 3 values for x/y/z coordinates)
- Include the source code snippet where the parameter is defined

#### Mutual exclusions and dependencies
- Parameters that conflict with each other
- Parameters that require other parameters to be set

#### Output files
- How output paths are constructed from input arguments
- Which outputs are conditional on flags
- Include the relevant code where output paths are built

## Output format

Write a markdown report. Structure it as:

```
# <tool_name>

<one-line description>

## Command pattern
`<invocation pattern>`

## Parsing approach
<what parser is used, which source files contain the parsing>

## Positional arguments
<ordered list with source snippets>

## Parameters
<each parameter with flag, type, default, help text, constraints, and source snippet>

## Mutual exclusions / dependencies
<if any>

## Output files
<how outputs are constructed, with source snippets>

## Sub-tools invoked
<list ONLY binaries/scripts that produce output files on behalf of this tool. \
Do NOT list utilities used internally (image math, statistics, registration, \
file manipulation, etc.) — only tools whose outputs are part of this tool's \
user-visible output. Omit this section entirely if there are none>

## Source files examined
<list of files you read>

## Uncertainties
<anything you weren't sure about>
```

## Guidelines

- Be thorough. Read the actual argument parsing code — don't guess from help strings alone.
- Quote help text verbatim — don't paraphrase.
- Don't interpret types for downstream agents — show the code and note what you observe \
(e.g. "parsed as float via atof" not "type: float").
- Don't skip parameters that seem internal or rarely used.
- Flag uncertainty explicitly rather than guessing.
- ALWAYS read the code where output file paths are constructed. Never guess output \
filenames from help text or conventions — find the actual write/save calls in the \
source and include the code. If you haven't read the output-writing code, your \
report is incomplete.
- If the tool delegates to other binaries or scripts (e.g. a wrapper calls a compiled \
tool, or one tool invokes another), trace the output generation through those calls. \
Read the sub-binary's source to find its actual output paths. Do not assume outputs \
based on the wrapper's flags alone.
- If the source file is large, make sure to read ALL sections relevant to argument \
parsing and output generation. Use offset/limit or read_tail to page through the \
file. Do not stop after reading only the first portion.
- If a source file has multiple code paths that produce outputs (e.g. different \
modes, conditional branches, separate main functions), read ALL of them. Use grep \
to find all file-writing calls (e.g. `save_volume`, `save`, `write`, `fopen`) and \
read the surrounding context for each.

## Source references

Every source snippet MUST be annotated with the file path (relative to repo root) \
and line number(s). Use this format consistently:

```
<!-- source: path/to/file.cpp:55-57 -->
```cpp
Option<bool> verbose(string("-v,--verbose"), false,
    string("switch on diagnostic messages"),
    false, no_argument);
```

The `<!-- source: ... -->` comment makes references machine-parseable for downstream \
tools. Always use the path as shown by the read_file tool (relative to repo root). \
Line numbers come from the numbered output of read_file.
"""


FSL_STRATEGY = """\

## Package-specific guidance: FSL

FSL tools use two argument parsing patterns:

### Pattern 1: Option<T> / OptionParser (newer tools like BET)
Look for `#include "utils/options.h"` and declarations like:
```cpp
Option<bool> verbose(string("-v,--verbose"), false,
    string("switch on diagnostic messages"),
    false, no_argument);
Option<float> threshold(string("-f"), 0.5,
    string("fractional intensity threshold (0->1)"),
    false, requires_argument);
```
Constructor: `Option<T>(key_string, default_value, help_text, compulsory, arg_flag)`
- `no_argument` = boolean flag
- `requires_argument` = takes 1 value
- `requires_N_arguments` = takes N values

### Pattern 2: Manual if/else chain (older tools like FLIRT)
Look for `parse_command_line` functions with:
```cpp
if (arg == "-bins") { no_bins = atoi(argv[n+1]); n+=2; continue; }
```
- `n+=2` means 1 argument, `n+=3` means 2 arguments, etc.
- `atoi` = int, `atof` = float, plain string = string

### Pattern 3: Shell script wrappers
Many FSL tools have a shell script that wraps a C++ binary and adds extra flags. \
These are separate tools with their own CLI. Look for argument parsing via \
`case`/`esac`, `getopts`, or manual `shift`-based loops.

### Where to look
- Main source files: `*.cc`, `*.cpp` in the tool's directory
- Shell scripts without extensions or with `.sh`
- Option headers: anything including `utils/options.h`
- Help/usage functions for descriptions
- Output path construction near file write operations
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
        f"First locate the source file where this tool's CLI is defined, "
        f"then extract all CLI parameters, output files, and relevant source code. "
        f"Produce a complete markdown report."
    )

    return await _run_agent(
        system_prompt=system_prompt,
        user_message=user_msg,
        repo_root=repo_root,
        model=model,
    )
