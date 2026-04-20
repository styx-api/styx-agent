"""Per-tool Explorer agents.

Two agents, run in sequence by the ``explore`` orchestrator:

- ``explore_interface`` — extracts inputs, constraints, parsing approach.
- ``explore_outputs`` — traces output file generation (uses the interface
  report as context).

The package-level strategy is produced by :mod:`styx_ai.scanner` and
auto-loaded into each agent's system prompt via ``load_strategy``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from styx_ai.agent import DEFAULT_MODEL
from styx_ai.explorer.interface import INTERFACE_PROMPT, explore_interface
from styx_ai.explorer.outputs import OUTPUT_PROMPT, explore_outputs
from styx_ai.scanner import explore_strategy

logger = logging.getLogger(__name__)

__all__ = [
    "INTERFACE_PROMPT",
    "OUTPUT_PROMPT",
    "explore",
    "explore_interface",
    "explore_outputs",
]


async def explore(
    tool_name: str,
    repo_path: str | Path,
    package: str = "fsl",
    model: str = DEFAULT_MODEL,
    cache_dir: str | Path | None = None,
    refresh_strategy: bool = False,
) -> str:
    """Run the full per-tool pipeline: ensure strategy → interface → outputs.

    Returns:
        Combined markdown report (interface + outputs).
    """
    await explore_strategy(
        package=package,
        repo_path=repo_path,
        model=model,
        cache_dir=cache_dir,
        refresh=refresh_strategy,
    )

    interface_report = await explore_interface(
        tool_name, repo_path, package=package, model=model, cache_dir=cache_dir,
    )
    logger.info("Interface report complete, starting output tracing...")

    output_report = await explore_outputs(
        tool_name, repo_path, interface_report,
        package=package, model=model, cache_dir=cache_dir,
    )

    return f"{interface_report}\n\n{output_report}"
