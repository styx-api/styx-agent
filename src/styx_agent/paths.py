"""Shared output-path conventions.

All artifacts land under ``<out_root>/<package>/``:

- Per-package scanner strategy files at ``<out_root>/<package>/_strategy/*.md``
- Per-tool Explorer + Author artifacts at ``<out_root>/<package>/<tool>/``
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_OUT_ROOT = Path("output")


def out_root(explicit: str | Path | None = None) -> Path:
    return Path(explicit) if explicit is not None else DEFAULT_OUT_ROOT


def package_dir(package: str, root: str | Path | None = None) -> Path:
    return out_root(root) / package


def strategy_dir(package: str, root: str | Path | None = None) -> Path:
    return package_dir(package, root) / "_strategy"


def tool_dir(package: str, tool: str, root: str | Path | None = None) -> Path:
    return package_dir(package, root) / tool


def runs_dir(root: str | Path | None = None) -> Path:
    """Directory holding timestamped campaign runs: ``<out_root>/runs/``."""
    return out_root(root) / "runs"


def run_dir(run_id: str, root: str | Path | None = None) -> Path:
    """A single campaign run's root: ``<out_root>/runs/<run_id>/``.

    The usual ``<package>/<tool>/`` artifact tree lives under this, so the
    scanner's per-package strategy cache is still shared across tools in the run.
    """
    return runs_dir(root) / run_id
