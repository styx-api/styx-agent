"""styx-agent: source-reading pipeline that generates Styx descriptors."""

from __future__ import annotations

from pathlib import Path

from styx_agent.agent import DEFAULT_MODEL
from styx_agent.author import author_boutiques
from styx_agent.explorer import explore_interface, explore_outputs
from styx_agent.paths import tool_dir
from styx_agent.scanner import explore_strategy

__all__ = ["wrap"]


async def wrap(
    tool_name: str,
    repo_path: str | Path,
    package: str = "fsl",
    target: str = "boutiques",
    model: str = DEFAULT_MODEL,
    out_root: str | Path | None = None,
    refresh_strategy: bool = False,
    max_retries: int = 3,
) -> Path:
    """Run the full pipeline: scan → interface → outputs → author.

    Writes three artifacts to ``<out_root>/<package>/<tool_name>/``:
    ``interface.md``, ``outputs.md``, ``boutiques.json`` (or whatever the
    target demands). Returns the path to the tool's output directory.
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
    output_report = await explore_outputs(
        tool_name, repo_path, interface_report,
        package=package, model=model, out_root=out_root,
    )

    dest = tool_dir(package, tool_name, out_root)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "interface.md").write_text(interface_report, encoding="utf-8")
    (dest / "outputs.md").write_text(output_report, encoding="utf-8")

    if target == "boutiques":
        descriptor = await author_boutiques(
            tool_name=tool_name,
            interface_report=interface_report,
            output_report=output_report,
            model=model,
            max_retries=max_retries,
        )
        (dest / "boutiques.json").write_text(descriptor, encoding="utf-8")
    else:
        raise ValueError(f"unknown author target: {target!r}")

    return dest
