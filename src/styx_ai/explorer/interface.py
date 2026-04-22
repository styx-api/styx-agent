"""Interface agent: extract CLI inputs and parsing approach from source code."""

from __future__ import annotations

from pathlib import Path

from styx_ai.agent import DEFAULT_MODEL, run_agent
from styx_ai.scanner import load_strategy

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
(by index), or other. For positional args, note whether the parser requires \
them in a specific position (e.g. must come last because the parser loop \
stops processing flags after it)
- **Constraints** — value ranges, allowed choices, dependencies on other inputs
- **Source snippet** — the code where this input is defined

### Constraints

Mutual exclusions, dependencies between inputs, and argument ordering \
requirements (e.g. positional args that must appear after all flags).

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

## Output discipline

Output ONLY the requested markdown report. Do not preface it with narrative \
("Okay, I have now gathered enough..."), commentary about your process, or \
notes about what you explored. The first line of your response must be the \
`# <tool_name>` heading. No trailing commentary either ("I'm ready to present \
the report", "Analysis complete", etc.).
"""


async def explore_interface(
    tool_name: str,
    repo_path: str | Path,
    package: str = "fsl",
    model: str = DEFAULT_MODEL,
    out_root: str | Path | None = None,
) -> str:
    """Extract CLI input interface from source code.

    Returns:
        Markdown report of inputs, constraints, and parsing approach.
    """
    repo_root = str(Path(repo_path).resolve())
    system_prompt = INTERFACE_PROMPT + load_strategy(package, out_root)

    return await run_agent(
        system_prompt=system_prompt,
        user_message=(
            f"Analyze the tool '{tool_name}' in this repository. "
            f"Produce a complete report of its input interface."
        ),
        repo_root=repo_root,
        model=model,
        label="interface",
    )
