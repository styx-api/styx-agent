"""Scan a repo for its layout and tool-enumeration conventions."""

from __future__ import annotations

from pathlib import Path

from styx_agent.agent import DEFAULT_MODEL, run_agent
from styx_agent.scanner._common import SCAN_PREAMBLE

ENUMERATION_SCAN_PROMPT = SCAN_PREAMBLE + """\

## Your specific job

Read the top-level repo structure and the build system (CMakeLists.txt, \
setup.py, pyproject.toml, Makefile, or whatever is present). Then produce a \
markdown reference covering:

1. **Orientation** — one short paragraph describing the repo's top-level \
layout (monorepo of many tools? single tool? mix?), build system, and \
whether tools are compiled binaries, scripts, or both.
2. **Locating a tool** — the OPERATIONAL rule for mapping a tool name to its \
entry-point file. State it concretely so the downstream agent can act on it: \
e.g. "for tool `X`, look at `src/X.c`" or "for tool `X`, look in `X/` \
subdirectory for `X.cc` and an optional shell wrapper named `X`". Include \
1-2 concrete examples.
3. **Tool enumeration** — how to list every CLI tool in this repo. What \
build-system signals to look for (`add_executable`, `entry_points`, \
`add_subdirectory`, script install globs, etc.), and how to distinguish \
real tools from helper / test / example binaries.

## Report format

Produce markdown with exactly these three subsections. First line must be \
the `### Orientation` header.

```
### Orientation

<One short paragraph.>

### Locating a tool

<Operational path pattern with 1-2 examples.>

### Tool enumeration

<Build-system signals, files to parse, how to distinguish real tools.>
```

Keep it tight — aim for ~30-50 lines total across all three subsections.

## Source references

Annotate each snippet with file path and line range:

<!-- source: path/to/file:55-57 -->
```cmake
code here
```
"""


async def scan_enumeration(
    repo_path: str | Path,
    package: str,
    model: str = DEFAULT_MODEL,
) -> str:
    repo_root = str(Path(repo_path).resolve())
    return await run_agent(
        system_prompt=ENUMERATION_SCAN_PROMPT,
        user_message=(
            f"Analyze the '{package}' source repository and produce the "
            f"layout and tool-enumeration reference."
        ),
        repo_root=repo_root,
        model=model,
        label="scan:enumeration",
    )
