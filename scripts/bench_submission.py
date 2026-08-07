"""Author descriptors from frozen Explorer reports and emit a styx-bench submission.

This is the adapter of the bench design's build order step 6 - the only piece
that lives on the harness side. styx-bench depends on ``@argtype/core`` and
knows nothing about styx-agent; the coupling runs one way, from here.

The mode is ``author-only``: Scanner and Explorer output is read off disk rather
than regenerated, so the Author model is the only thing that varies between two
submissions. That is what makes a comparison a comparison - and it is cheap,
roughly one LLM call per tool instead of the ten-plus an outputs trace costs.

    # once, in styx-bench
    node dist/cli.js list --json manifest.json

    # here, per Author model under test
    python scripts/bench_submission.py \
        --manifest manifest.json \
        --reports bench/frozen/<corpus-sha>-<explorer-model> \
        --model neurodesk/glm-5.2 \
        --out submissions/glm-5.2.json

    # back in styx-bench
    node dist/cli.js run submissions/glm-5.2.json

A submission is keyed by challenge; the Author works per tool. One document is
authored per distinct host tool and shared by every challenge about that tool,
which is exactly what the help-parser baseline does on the bench side.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from styx_agent.author.argtype import author_argtype  # noqa: E402
from styx_agent.author.argtype_validator import BridgeUnavailable, ensure_bridge  # noqa: E402
from styx_agent.telemetry import collect_agent_stats  # noqa: E402


def _tool_key(host: dict) -> str:
    return f"{host.get('repo')}/{host.get('tool')}"


async def _author_one(tool: str, reports: Path, model: str, max_retries: int) -> tuple[str | None, str | None, dict]:
    """Author one tool. Returns (source, error, stats); exactly one of the first two is set."""
    interface = reports / "interface.md"
    outputs = reports / "outputs.md"
    missing = [p.name for p in (interface, outputs) if not p.is_file()]
    if missing:
        # An abstention, not a bench error: the reports are this system's own
        # input, so failing to have them is a fact about the system under test.
        return None, f"no frozen report: {', '.join(missing)} missing in {reports}", {}

    started = time.monotonic()
    with collect_agent_stats() as stats:
        try:
            source = await author_argtype(
                tool,
                interface.read_text(encoding="utf-8"),
                outputs.read_text(encoding="utf-8"),
                model=model,
                max_retries=max_retries,
            )
            error = None
        except Exception as e:  # noqa: BLE001 - a failed author is a result, not a crash
            source, error = None, f"{type(e).__name__}: {e}"

    usage = {
        "tokens": sum(s.total_tokens for s in stats),
        "turns": sum(s.turns for s in stats),
        "seconds": round(time.monotonic() - started, 1),
    }
    return source, error, usage


async def _run(args: argparse.Namespace) -> int:
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    challenges = manifest["challenges"]

    # Group challenges by the tool they are about, so each tool is authored once.
    by_tool: dict[str, list[str]] = defaultdict(list)
    hosts: dict[str, dict] = {}
    for challenge in challenges:
        host = challenge.get("host")
        if not host or not host.get("tool"):
            continue
        key = _tool_key(host)
        by_tool[key].append(challenge["id"])
        hosts[key] = host

    unhosted = [c["id"] for c in challenges if not c.get("host", {}).get("tool")]
    if unhosted:
        print(f"warning: {len(unhosted)} challenge(s) name no host tool and will be unanswered", file=sys.stderr)

    reports_root = Path(args.reports)
    answers: dict[str, dict] = {}
    total_tokens = 0

    for key in sorted(by_tool):
        host = hosts[key]
        reports = reports_root / str(host.get("repo")) / str(host.get("tool"))
        print(f"authoring {key} ({len(by_tool[key])} challenges) ...", file=sys.stderr, flush=True)
        source, error, usage = await _author_one(host["tool"], reports, args.model, args.max_retries)
        total_tokens += usage.get("tokens", 0)

        if source is not None:
            print(f"  ok: {len(source)} chars, {usage.get('tokens', 0)} tokens", file=sys.stderr)
        else:
            print(f"  abstained: {error}", file=sys.stderr)

        answer = {"source": source} if source is not None else {"error": error}
        # Cost is attributed to the tool's first challenge rather than divided:
        # one authoring call served all of them, and splitting it would invent a
        # per-challenge figure that was never paid.
        for i, challenge_id in enumerate(by_tool[key]):
            answers[challenge_id] = {**answer, **({"cost": {"tokens": usage.get("tokens", 0)}} if i == 0 else {})}

    # Optional fields are omitted rather than sent as null: the bench validates
    # types, so `"version": null` is rejected outright where an absent key is
    # fine.
    submission = {
        "system": args.system or f"styx-agent/{args.model}",
        "notes": (
            f"author-only against frozen reports in {reports_root.name}; "
            f"Author model {args.model}, max_retries {args.max_retries}"
        ),
        "cost": {"tokens": total_tokens},
        "answers": answers,
    }
    if args.version is not None:
        submission["version"] = args.version
    if args.run is not None:
        submission["run"] = args.run

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(submission, indent=2) + "\n", encoding="utf-8")

    answered = sum(1 for a in answers.values() if "source" in a)
    print(
        f"wrote {len(answers)} answers ({answered} authored, {len(answers) - answered} abstained, "
        f"{total_tokens} tokens) to {out}",
        file=sys.stderr,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", required=True, help="from `styx-bench list --json FILE`")
    parser.add_argument("--reports", required=True, help="frozen report root: <dir>/<package>/<tool>/{interface,outputs}.md")
    parser.add_argument("--out", required=True, help="where to write the submission JSON")
    parser.add_argument("--model", default=None, help="Author model (default: the agent's own default)")
    parser.add_argument("--system", default=None, help="submission `system` name (default: styx-agent/<model>)")
    parser.add_argument("--version", default=None, help="submission `version`")
    parser.add_argument("--run", type=int, default=None, help="repeat index; design asks for n >= 3")
    parser.add_argument("--max-retries", type=int, default=3)
    args = parser.parse_args()

    if args.model is None:
        from styx_agent.agent import DEFAULT_MODEL

        args.model = DEFAULT_MODEL

    try:
        # Fail before spending tokens if the validation bridge is broken.
        ensure_bridge()
    except BridgeUnavailable as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
