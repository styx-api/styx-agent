"""styx-agent: source-reading pipeline that generates Styx descriptors."""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path

from styx_agent.agent import DEFAULT_MODEL
from styx_agent.author import author_boutiques
from styx_agent.explorer import explore_interface, explore_outputs
from styx_agent.paths import run_dir, tool_dir
from styx_agent.scanner import explore_strategy
from styx_agent.telemetry import collect_agent_stats

logger = logging.getLogger(__name__)

__all__ = ["wrap", "wrap_all"]


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


async def wrap_all(
    tools: list[str],
    repo_path: str | Path,
    run_id: str,
    package: str = "fsl",
    target: str = "boutiques",
    model: str = DEFAULT_MODEL,
    out_root: str | Path | None = None,
    max_retries: int = 3,
    refresh_strategy: bool = False,
    started_at: str | None = None,
) -> Path:
    """Campaign: wrap many tools into one timestamped run dir with stats.

    Writes, under ``<out_root>/runs/<run_id>/``:

    - ``run.json`` — params, provenance (model, git SHAs), and aggregates.
    - ``results.jsonl`` — one row per tool, appended as each finishes (so a
      crashed campaign keeps partial data).
    - ``<package>/<tool>/`` — the usual artifacts plus a per-tool ``meta.json``.

    A single failing tool is recorded and the campaign continues.
    """
    run_root = run_dir(run_id, out_root)
    run_root.mkdir(parents=True, exist_ok=True)
    results_path = run_root / "results.jsonl"
    results_path.write_text("", encoding="utf-8")

    provenance = {
        "run_id": run_id,
        "started_at": started_at,
        "model": model,
        "package": package,
        "target": target,
        "max_retries": max_retries,
        "tools_requested": list(tools),
        "repo_path": str(Path(repo_path).resolve()),
        "styx_agent_sha": _git_sha(Path(__file__).resolve().parent),
        "source_repo_sha": _repo_sha_if_own_checkout(repo_path),
    }
    _write_json(run_root / "run.json", {**provenance, "status": "running"})

    # Scan the package strategy once up front so per-tool stats aren't skewed by
    # whichever tool happens to trigger the (otherwise cached) scan.
    with collect_agent_stats() as scan_stats:
        await explore_strategy(
            package=package, repo_path=repo_path, model=model,
            out_root=run_root, refresh=refresh_strategy,
        )
    strategy = {
        "agents": [s.to_dict() for s in scan_stats],
        "seconds": round(sum(s.seconds for s in scan_stats), 2),
        "tokens": sum(s.total_tokens for s in scan_stats),
    }

    results: list[dict] = []
    for tool in tools:
        record = await _wrap_one(
            tool, repo_path, package, target, model, run_root, max_retries, run_id,
        )
        results.append(record)
        with results_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        logger.info(
            f"[wrap-all] {tool}: {record['status']} "
            f"({record['seconds']}s, {record['tokens']} tok)"
        )

    aggregates = {
        "n_tools": len(results),
        "n_ok": sum(1 for r in results if r["status"] == "ok"),
        "n_failed": sum(1 for r in results if r["status"] != "ok"),
        "total_tokens": strategy["tokens"] + sum(r["tokens"] for r in results),
        "total_seconds": round(
            strategy["seconds"] + sum(r["seconds"] for r in results), 2
        ),
    }
    _write_json(run_root / "run.json", {
        **provenance, "status": "done", "strategy": strategy, "aggregates": aggregates,
    })
    return run_root


async def _wrap_one(
    tool: str,
    repo_path: str | Path,
    package: str,
    target: str,
    model: str,
    run_root: Path,
    max_retries: int,
    run_id: str,
) -> dict:
    """Wrap one tool, capturing stats and outcome; never raises."""
    t0 = time.monotonic()
    status = "ok"
    errors: list[str] = []
    with collect_agent_stats() as stats:
        try:
            dest = await wrap(
                tool_name=tool, repo_path=repo_path, package=package, target=target,
                model=model, out_root=run_root, max_retries=max_retries,
            )
        except ValueError as e:  # author gave up after retries
            status = "invalid"
            errors = [str(e)]
            dest = tool_dir(package, tool, run_root)
        except Exception as e:  # scan/explore crash, IO, etc. — keep the campaign going
            status = "error"
            errors = [f"{type(e).__name__}: {e}"]
            dest = tool_dir(package, tool, run_root)

    record = {
        "tool": tool,
        "package": package,
        "status": status,
        "seconds": round(time.monotonic() - t0, 2),
        "tokens": sum(s.total_tokens for s in stats),
        "agents": [s.to_dict() for s in stats],
        "errors": errors,
        **_descriptor_summary(dest),
    }
    dest.mkdir(parents=True, exist_ok=True)
    _write_json(dest / "meta.json", {"run_id": run_id, "model": model, **record})
    return record


def _descriptor_summary(dest: Path) -> dict:
    """Cheap grounding/size proxies from a tool's artifacts."""
    summary: dict = {}
    descriptor = dest / "boutiques.json"
    if descriptor.exists():
        try:
            data = json.loads(descriptor.read_text(encoding="utf-8"))
            summary["n_inputs"] = len(data.get("inputs") or [])
            summary["n_outputs"] = len(data.get("output-files") or [])
        except (json.JSONDecodeError, OSError):
            pass
    refs = 0
    for name in ("interface.md", "outputs.md"):
        report = dest / name
        if report.exists():
            refs += report.read_text(encoding="utf-8").count("<!-- source:")
    summary["n_source_refs"] = refs
    return summary


def _git_sha(path: str | Path) -> str | None:
    """Best-effort HEAD SHA of the git repo *containing* ``path`` (None if none)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _repo_sha_if_own_checkout(path: str | Path) -> str | None:
    """HEAD SHA only if ``path`` is the top level of its OWN git checkout.

    Source repos under ``repos/`` may have no ``.git`` of their own; a plain
    ``git -C`` there would walk up and report *this* repo's SHA, which is
    misleading provenance. Returning None is the honest answer in that case.
    """
    try:
        top = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != Path(path).resolve():
        return None
    return _git_sha(path)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
