"""Scanner: per-package scan agents that infer repo-level conventions.

Three focused scan agents run per package. The enumeration scan runs first;
its report is passed as context to the parsing and outputs scans, which then
run in parallel. The output is cached as three per-section files at
``<out_root>/<package>/_strategy/<section>.md``. ``load_strategy`` reads and
concatenates them under a single ``## Package: <name>`` header.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from styx_agent.agent import DEFAULT_MODEL
from styx_agent.paths import strategy_dir
from styx_agent.scanner.enumeration import scan_enumeration
from styx_agent.scanner.outputs import scan_outputs
from styx_agent.scanner.parsing import scan_parsing

logger = logging.getLogger(__name__)

SECTIONS = ("enumeration", "parsing", "outputs")

__all__ = [
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
    out_root: str | Path | None = None,
    refresh: bool = False,
) -> str:
    """Produce (or load from cache) the package-specific strategy document.

    The enumeration scan runs first; its report is passed to the parsing and
    outputs scans, which run in parallel.
    """
    pkg_dir = strategy_dir(package, out_root)
    section_paths = {s: pkg_dir / f"{s}.md" for s in SECTIONS}

    if all(p.exists() for p in section_paths.values()) and not refresh:
        logger.info(f"[strategy] using cached strategy at {pkg_dir}")
        return load_strategy(package, out_root)

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

    return load_strategy(package, out_root)


def load_strategy(
    package: str,
    out_root: str | Path | None = None,
) -> str:
    """Load cached per-section strategy files and concatenate them.

    Returns an empty string if no cache exists (per-tool agents will run
    without package-specific context; caller should run ``explore_strategy``
    first for best results).
    """
    pkg_dir = strategy_dir(package, out_root)
    fragments: list[str] = []
    for section in SECTIONS:
        path = pkg_dir / f"{section}.md"
        if path.exists():
            fragments.append(path.read_text(encoding="utf-8").strip())

    if not fragments:
        return ""
    body = "\n\n".join(fragments)
    return f"\n\n## Package: {package}\n\n{body}\n"
