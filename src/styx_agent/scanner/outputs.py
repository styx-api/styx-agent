"""Scan a repo for its output-file-writing conventions."""

from __future__ import annotations

from pathlib import Path

from styx_agent.agent import DEFAULT_MODEL, run_agent
from styx_agent.scanner._common import SCAN_PREAMBLE

OUTPUT_SCAN_PROMPT = SCAN_PREAMBLE + """\

## Your specific job

**You MUST pick at least one tool that writes data files to disk** (NIFTI, \
images, datasets, text tables, etc.). Do NOT rely on info/query tools that \
only print to stdout — they teach you nothing about file outputs. If the \
first tool you open only prints to stdout, move on and open another until \
you find one that writes a file.

Produce a markdown reference covering:

1. **Write functions** — a bullet list of concrete, grep-able function or \
macro names used to write output files (e.g. `DSET_write`, `save_volume`, \
`THD_write_3dim_dataset`, `nib.save`). Real names only — no "functions from \
library X".
2. **Filename construction** — the idiom by which output filenames are built \
from input names and flags. Show a 3-6 line code snippet of a real \
construction. Note whether wrappers/macros auto-append extensions.
3. **Wrappers & delegation** — does this package ship separate installable \
entry points (shell scripts, Python scripts, or wrapper binaries) that \
**invoke OTHER installed tools as sub-processes** (e.g. FSL's `bet` shell \
script runs `bet2` and `betsurf` as separate binaries)? C/C++ code inside \
one tool that calls library functions is NOT a wrapper. If no true wrappers \
exist, say "None observed."
4. **Extension conventions** — the common output extensions in this package \
(e.g. `.nii.gz`, `+orig.HEAD` + `+orig.BRIK` pair, `.1D`).

## Report format

Produce markdown with exactly these two subsections. First line must be the \
`### Outputs` header.

```
### Outputs

<Write functions list. Filename idiom with snippet. Extension conventions.>

### Wrappers & delegation

<Whether wrapper scripts exist (as defined above); how to detect; how to \
follow their sub-binary calls. If no true wrappers exist, write \
"None observed.">
```

Keep it tight — aim for ~30-50 lines total across both subsections.

## Source references

Annotate each snippet with file path and line range:

<!-- source: path/to/file.cpp:55-57 -->
```cpp
code here
```
"""


async def scan_outputs(
    repo_path: str | Path,
    package: str,
    model: str = DEFAULT_MODEL,
    enumeration_report: str | None = None,
) -> str:
    repo_root = str(Path(repo_path).resolve())
    user_message = (
        f"Analyze the '{package}' source repository and produce the "
        f"output-writing-conventions reference."
    )
    if enumeration_report:
        user_message += (
            f"\n\nThe enumeration scan has already produced this layout "
            f"reference — use it to jump straight to a data-producing tool:"
            f"\n\n---\n\n{enumeration_report}"
        )
    return await run_agent(
        system_prompt=OUTPUT_SCAN_PROMPT,
        user_message=user_message,
        repo_root=repo_root,
        model=model,
        label="scan:outputs",
    )
