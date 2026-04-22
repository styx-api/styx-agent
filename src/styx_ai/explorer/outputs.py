"""Output agent: trace output file generation through source code."""

from __future__ import annotations

from pathlib import Path

from styx_ai.agent import DEFAULT_MODEL, run_agent
from styx_ai.scanner import load_strategy

OUTPUT_PROMPT = """\
You are a source code researcher. You trace output file generation in source \
repositories.

You will be given a tool name and an interface report from a previous analysis. \
Your job is to find every file the tool writes to disk.

## What to extract

For every output file, document:

- **Path pattern** — complete filename(s) including extensions, as they appear \
on disk. Trace from the write call back to where the filename string is built. \
If the write function is a wrapper/macro that adds extensions, find its \
definition in the source tree (search headers too, not just the tool's file).
- **Condition** — which inputs control whether this file is produced
- **Source snippet** — the write call and the filename construction

## Report format

Write the report directly as markdown (do NOT wrap it in a code block):

- `# <tool_name> — Outputs`
- `## Outputs` — for each output: path pattern, condition, source snippet
- `## Source files examined` — files you read
- `## Uncertainties` — anything you investigated but could not resolve

Before reporting an uncertainty, grep for it in the source tree first. \
Only report it after you have looked and could not find the answer.

## Source references

Annotate every source snippet with file path and line numbers:

<!-- source: path/to/file.cpp:55-57 -->
```cpp
code here
```

## Output discipline

Output ONLY the requested markdown report. Do not preface it with narrative \
("Okay, I have now gathered enough..."), commentary about your process, or \
notes about what you explored. The first line of your response must be the \
`# <tool_name> — Outputs` heading. No trailing commentary either \
("I'm ready to present the report", "Analysis complete", etc.).
"""


async def explore_outputs(
    tool_name: str,
    repo_path: str | Path,
    interface_report: str,
    package: str = "fsl",
    model: str = DEFAULT_MODEL,
    out_root: str | Path | None = None,
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
    system_prompt = OUTPUT_PROMPT + load_strategy(package, out_root)

    return await run_agent(
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
        label="outputs",
    )
