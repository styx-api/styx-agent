"""Styx AI: source-to-descriptor pipeline."""

from __future__ import annotations

from pathlib import Path

from styx_ai.agent import DEFAULT_MODEL
from styx_ai.author import author_boutiques
from styx_ai.explorer import explore_interface, explore_outputs
from styx_ai.scanner import explore_strategy

__all__ = ["wrap"]


async def wrap(
    tool_name: str,
    repo_path: str | Path,
    package: str = "fsl",
    target: str = "boutiques",
    model: str = DEFAULT_MODEL,
    cache_dir: str | Path | None = None,
    refresh_strategy: bool = False,
    max_retries: int = 3,
) -> str:
    """Run the full pipeline: scan → interface → outputs → author.

    Returns the descriptor JSON as a string.
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
    output_report = await explore_outputs(
        tool_name, repo_path, interface_report,
        package=package, model=model, cache_dir=cache_dir,
    )

    if target == "boutiques":
        return await author_boutiques(
            tool_name=tool_name,
            interface_report=interface_report,
            output_report=output_report,
            model=model,
            max_retries=max_retries,
        )
    raise ValueError(f"unknown author target: {target!r}")
