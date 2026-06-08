"""Per-tool Explorer agents.

Two agents, run in sequence by the ``explore`` orchestrator:

- ``explore_interface`` — extracts inputs, constraints, parsing approach.
- ``explore_outputs`` — traces output file generation (uses the interface
  report as context).

The package-level strategy is produced by :mod:`styx_agent.scanner` and
auto-loaded into each agent's system prompt via ``load_strategy``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from styx_agent.agent import DEFAULT_MODEL
from styx_agent.explorer.interface import INTERFACE_PROMPT, explore_interface
from styx_agent.explorer.outputs import OUTPUT_PROMPT, explore_outputs
from styx_agent.scanner import explore_strategy

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
    out_root: str | Path | None = None,
    refresh_strategy: bool = False,
) -> tuple[str, str]:
    """Run the full per-tool pipeline: ensure strategy → interface → outputs.

    Returns:
        (interface_report, output_report) as separate strings.
    """
    await explore_strategy(
        package=package,
        repo_path=repo_path,
        model=model,
        out_root=out_root,
        refresh=refresh_strategy,
    )

    interface_report = await explore_interface(
        tool_name, repo_path, package=package, model=model, out_root=out_root,
    )
    logger.info("Interface report complete, starting output tracing...")

    output_report = await explore_outputs(
        tool_name, repo_path, interface_report,
        package=package, model=model, out_root=out_root,
    )

    return interface_report, output_report
