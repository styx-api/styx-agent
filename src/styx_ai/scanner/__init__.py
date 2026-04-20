"""Scanner: per-package scan agents that infer repo-level conventions.

Three focused scan agents run per package. The enumeration scan runs first;
its report is passed as context to the parsing and outputs scans, which then
run in parallel. The output is cached as three per-section files at
``output/_strategies/<package>/<section>.md``. ``load_strategy`` reads and
concatenates them under a single ``## Package: <name>`` header.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from styx_ai.agent import DEFAULT_MODEL
from styx_ai.scanner.enumeration import scan_enumeration
from styx_ai.scanner.outputs import scan_outputs
from styx_ai.scanner.parsing import scan_parsing

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path("output/_strategies")
SECTIONS = ("enumeration", "parsing", "outputs")

__all__ = [
    "DEFAULT_CACHE_DIR",
    "SECTIONS",
    "explore_strategy",
    "load_strategy",
    "scan_enumeration",
    "scan_outputs",
    "scan_parsing",
]


async def explore_strategy(
    package: str,
    repo_path: str | Path,
    model: str = DEFAULT_MODEL,
    cache_dir: str | Path | None = None,
    refresh: bool = False,
) -> str:
    """Produce (or load from cache) the package-specific strategy document.

    The enumeration scan runs first; its report is passed to the parsing and
    outputs scans, which run in parallel.
    """
    pkg_dir = _package_dir(package, cache_dir)
    section_paths = {s: pkg_dir / f"{s}.md" for s in SECTIONS}

    if all(p.exists() for p in section_paths.values()) and not refresh:
        logger.info(f"[strategy] using cached strategy at {pkg_dir}")
        return load_strategy(package, cache_dir)

    repo_root = str(Path(repo_path).resolve())
    logger.info(f"[strategy] scanning '{package}' at {repo_root}")

    pkg_dir.mkdir(parents=True, exist_ok=True)

    enumeration = await scan_enumeration(repo_root, package, model)
    section_paths["enumeration"].write_text(enumeration, encoding="utf-8")
    logger.info(f"[strategy] enumeration cached to {section_paths['enumeration']}")

    parsing, outputs = await asyncio.gather(
        scan_parsing(repo_root, package, model, enumeration_report=enumeration),
        scan_outputs(repo_root, package, model, enumeration_report=enumeration),
    )
    section_paths["parsing"].write_text(parsing, encoding="utf-8")
    section_paths["outputs"].write_text(outputs, encoding="utf-8")
    logger.info(f"[strategy] parsing + outputs cached to {pkg_dir}")

    return load_strategy(package, cache_dir)


def load_strategy(
    package: str,
    cache_dir: str | Path | None = None,
) -> str:
    """Load cached per-section strategy files and concatenate them.

    Falls back to a hand-written strategy when no cache exists, else empty.
    """
    pkg_dir = _package_dir(package, cache_dir)
    fragments: list[str] = []
    for section in SECTIONS:
        path = pkg_dir / f"{section}.md"
        if path.exists():
            fragments.append(path.read_text(encoding="utf-8").strip())

    if fragments:
        body = "\n\n".join(fragments)
        return f"\n\n## Package: {package}\n\n{body}\n"

    return _FALLBACK_STRATEGIES.get(package, "")


def _package_dir(package: str, cache_dir: str | Path | None) -> Path:
    base = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
    return base / package


# ---------------------------------------------------------------------------
# Hand-written fallbacks used when no cached strategy exists.
# Keep until generated strategies are validated across packages.
# ---------------------------------------------------------------------------

_FSL_FALLBACK = """\

## Package: fsl

### Orientation

FSL is a monorepo where each tool lives in its own top-level subdirectory. \
Most tools are C++ binaries with optional shell wrapper scripts.

### Locating a tool

For tool `X`, look in `X/` subdirectory: `X/X.cc` or `X/X.cpp` for the \
binary source, and an optional shell script named `X` (no extension) for a \
wrapper.

### Tool enumeration

Scan top-level subdirectories. Each contains a Makefile listing the binaries \
it builds. Shell wrappers sit alongside the binaries they wrap.

### Parsing & help text

**Option<T> / OptionParser** — `#include "utils/options.h"`:
```cpp
Option<float> threshold(string("-f"), 0.5,
    string("fractional intensity threshold (0->1)"),
    false, requires_argument);
```
Constructor: `Option<T>(keys, default, help_text, compulsory, arg_flag)`.
- `no_argument` = boolean, `requires_argument` = 1 value, `requires_N_arguments` = N values.

**Manual if/else** — older tools:
```cpp
if (arg == "-bins") { no_bins = atoi(argv[n+1]); n+=2; continue; }
```

**Shell wrappers** — `case`/`esac`, `getopts`, or `shift` loops.

Help text is the third constructor argument to `Option<T>`; quote it verbatim.

### Types

- `Option<float>` / `atof` → float
- `Option<int>` / `atoi` → int
- `Option<bool>` with `no_argument` → boolean
- `Option<string>` → string
- `Option<std::vector<T>>` with `requires_N_arguments` → fixed-length tuple

### Outputs

FSL tools typically write outputs via `save_volume`, `save_volume4D`, or mesh \
`.save()` calls. Filenames are usually constructed by appending suffixes to \
a base output name (e.g. `out + "_mask"`). Common extension: `.nii.gz`.

### Wrappers & delegation

Shell wrapper scripts produce additional outputs via `fslmaths`, `immv`, or \
by calling sub-binaries like `betsurf`. Trace these calls to find actual \
output paths.
"""

_FALLBACK_STRATEGIES: dict[str, str] = {
    "fsl": _FSL_FALLBACK,
}
