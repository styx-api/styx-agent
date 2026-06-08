"""Scan a repo for its argument-parsing conventions."""

from __future__ import annotations

from pathlib import Path

from styx_agent.agent import DEFAULT_MODEL, run_agent
from styx_agent.scanner._common import SCAN_PREAMBLE

PARSING_SCAN_PROMPT = SCAN_PREAMBLE + """\

## Your specific job

Pick 2-3 tool entry points and read their full argument-parsing code. \
Produce a concise markdown reference covering:

1. **Parsing idiom** — the shape of option-parsing code, with a real 3-8 line \
code snippet. If the package has more than one idiom (e.g. a custom option \
class AND manual `if/else` in older tools), show both.
2. **Key includes/imports** — headers or imports that signal parsing code.
3. **Help text** — WHERE help text lives and HOW it is produced (inline \
constructor argument, separate `Syntax()`/`usage()` function, `--help` \
branch, shell heredoc, etc.). Include a snippet showing how to extract help \
text verbatim.
4. **Types** — how input types are expressed in source. List 3-6 common \
patterns as bullets, each with a minimal real example (e.g. \
"`atof(argv[i])` → float").

## Report format

Produce markdown with exactly these two subsections. First line must be the \
`### Parsing & help text` header.

```
### Parsing & help text

<Idiom with code snippet. Key includes. Help-text location with snippet.>

### Types

<Bullet list of type-expression patterns, each with a minimal example.>
```

Keep it tight — aim for ~30-50 lines total across both subsections.

## Source references

Annotate each snippet with file path and line range:

<!-- source: path/to/file.cpp:55-57 -->
```cpp
code here
```
"""


async def scan_parsing(
    repo_path: str | Path,
    package: str,
    model: str = DEFAULT_MODEL,
    enumeration_report: str | None = None,
) -> str:
    repo_root = str(Path(repo_path).resolve())
    user_message = (
        f"Analyze the '{package}' source repository and produce the "
        f"parsing-conventions reference."
    )
    if enumeration_report:
        user_message += (
            f"\n\nThe enumeration scan has already produced this layout "
            f"reference — use it to jump straight to tool entry points:\n\n"
            f"---\n\n{enumeration_report}"
        )
    return await run_agent(
        system_prompt=PARSING_SCAN_PROMPT,
        user_message=user_message,
        repo_root=repo_root,
        model=model,
        label="scan:parsing",
    )
