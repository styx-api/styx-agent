"""Top-level CLI for styx-agent.

All commands write to a conventional layout under ``--out-root`` (default
``output/``):

    <out_root>/<package>/_strategy/{enumeration,parsing,outputs}.md
    <out_root>/<package>/<tool>/{interface,outputs}.md
    <out_root>/<package>/<tool>/boutiques.json

Subcommands:

    styx-agent scan <repo>                           # per-package strategy scan
    styx-agent explore <tool> <repo>                 # per-tool exploration (strategy + interface + outputs)
    styx-agent explore <tool> <repo> --interface-only
    styx-agent explore <tool> <repo> --outputs-only
    styx-agent author <tool>                         # translate tool's cached reports into a descriptor
    styx-agent wrap <tool> <repo>                    # full pipeline: scan → interface → outputs → author
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from styx_agent import wrap, wrap_all
from styx_agent.author import author_boutiques
from styx_agent.explorer import explore, explore_interface, explore_outputs
from styx_agent.paths import strategy_dir, tool_dir
from styx_agent.scanner import explore_strategy
from styx_agent.toollist import read_tool_list
from styx_agent.tools.filesystem import require_grep


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _load_dotenv() -> None:
    env_file = os.path.join(os.getcwd(), ".env")
    if not os.path.isfile(env_file):
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("LiteLLM").setLevel(logging.WARNING if not verbose else logging.DEBUG)
    logging.getLogger("litellm").setLevel(logging.WARNING if not verbose else logging.DEBUG)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--package", default="fsl", help="Package identifier (default: fsl)")
    p.add_argument("--model", default=None, help="LLM model override")
    p.add_argument(
        "--out-root", default=None,
        help="Output root directory (default: output/)",
    )
    p.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")


def _model_kwarg(args: argparse.Namespace) -> dict:
    return {"model": args.model} if args.model else {}


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content + ("\n" if not content.endswith("\n") else ""), encoding="utf-8")
    print(f"Wrote {path}", file=sys.stderr)


def main() -> None:
    _configure_stdout()
    _load_dotenv()

    parser = argparse.ArgumentParser(
        prog="styx-agent",
        description="Source-reading agent pipeline that generates Styx descriptors",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # styx-agent scan <repo>
    scan_p = subparsers.add_parser(
        "scan",
        help="Produce a package-level strategy document (parsing + outputs + enumeration)",
    )
    scan_p.add_argument("repo", help="Path to cloned source repository")
    scan_p.add_argument(
        "--refresh", action="store_true",
        help="Regenerate even if a cached strategy exists",
    )
    _add_common_args(scan_p)

    # styx-agent explore <tool> <repo>
    explore_p = subparsers.add_parser(
        "explore",
        help="Run per-tool exploration (strategy + interface + outputs)",
    )
    explore_p.add_argument("tool", help="Tool command name (e.g. 'bet', '3dTstat')")
    explore_p.add_argument("repo", help="Path to cloned source repository")
    explore_p.add_argument(
        "--refresh-strategy", action="store_true",
        help="Regenerate the package strategy even if a cached copy exists",
    )
    mode = explore_p.add_mutually_exclusive_group()
    mode.add_argument(
        "--interface-only", action="store_true",
        help="Only extract input interface (skip output tracing)",
    )
    mode.add_argument(
        "--outputs-only", action="store_true",
        help="Only trace outputs (requires an existing interface.md)",
    )
    explore_p.add_argument(
        "--interface-report",
        help=(
            "Path to interface report for --outputs-only mode "
            "(default: <out-root>/<package>/<tool>/interface.md)"
        ),
    )
    _add_common_args(explore_p)

    # styx-agent author <tool>
    author_p = subparsers.add_parser(
        "author",
        help="Translate a tool's cached reports into a descriptor",
    )
    author_p.add_argument("tool", help="Tool name")
    author_p.add_argument(
        "--interface-report",
        help="Override path to interface report (default: <out-root>/<package>/<tool>/interface.md)",
    )
    author_p.add_argument(
        "--outputs-report",
        help="Override path to outputs report (default: <out-root>/<package>/<tool>/outputs.md)",
    )
    author_p.add_argument(
        "--target", default="boutiques", choices=("boutiques",),
        help="Descriptor target format",
    )
    author_p.add_argument(
        "--max-retries", type=int, default=3,
        help="Retries when validation fails (default: 3)",
    )
    _add_common_args(author_p)

    # styx-agent wrap <tool> <repo>
    wrap_p = subparsers.add_parser(
        "wrap",
        help="Full pipeline: scan → interface → outputs → author",
    )
    wrap_p.add_argument("tool", help="Tool command name")
    wrap_p.add_argument("repo", help="Path to cloned source repository")
    wrap_p.add_argument(
        "--target", default="boutiques", choices=("boutiques",),
        help="Descriptor target format",
    )
    wrap_p.add_argument(
        "--refresh-strategy", action="store_true",
        help="Regenerate the package strategy even if a cached copy exists",
    )
    wrap_p.add_argument(
        "--max-retries", type=int, default=3,
        help="Author retries on validation failure (default: 3)",
    )
    _add_common_args(wrap_p)

    # styx-agent wrap-all <repo> --tools a,b,c
    wrap_all_p = subparsers.add_parser(
        "wrap-all",
        help="Campaign: wrap many tools into a timestamped run dir with stats",
    )
    wrap_all_p.add_argument("repo", help="Path to cloned source repository")
    tools_src = wrap_all_p.add_mutually_exclusive_group(required=True)
    tools_src.add_argument(
        "--tools",
        help="Comma-separated tool names (e.g. 'bet,fast,flirt')",
    )
    tools_src.add_argument(
        "--tools-file",
        help=(
            "Tool list source: a newline-delimited text file, a JSON file "
            "(array of names / descriptor objects, or one descriptor), or a "
            "directory of NiWrap descriptor .json files"
        ),
    )
    wrap_all_p.add_argument(
        "--target", default="boutiques", choices=("boutiques",),
        help="Descriptor target format",
    )
    wrap_all_p.add_argument(
        "--refresh-strategy", action="store_true",
        help="Regenerate the package strategy even if a cached copy exists",
    )
    wrap_all_p.add_argument(
        "--max-retries", type=int, default=3,
        help="Author retries on validation failure (default: 3)",
    )
    _add_common_args(wrap_all_p)

    args = parser.parse_args()
    _configure_logging(args.verbose)
    require_grep()

    if args.command == "scan":
        asyncio.run(
            explore_strategy(
                package=args.package,
                repo_path=args.repo,
                out_root=args.out_root,
                refresh=args.refresh,
                **_model_kwarg(args),
            )
        )
        print(f"Strategy cached at {strategy_dir(args.package, args.out_root)}", file=sys.stderr)

    elif args.command == "explore":
        dest = tool_dir(args.package, args.tool, args.out_root)
        if args.interface_only:
            report = asyncio.run(
                explore_interface(
                    tool_name=args.tool, repo_path=args.repo,
                    package=args.package, out_root=args.out_root,
                    **_model_kwarg(args),
                )
            )
            _write_file(dest / "interface.md", report)
        elif args.outputs_only:
            iface_path = (
                Path(args.interface_report)
                if args.interface_report else dest / "interface.md"
            )
            if not iface_path.exists():
                parser.error(f"interface report not found at {iface_path}")
            interface_report = iface_path.read_text(encoding="utf-8")
            report = asyncio.run(
                explore_outputs(
                    tool_name=args.tool, repo_path=args.repo,
                    interface_report=interface_report,
                    package=args.package, out_root=args.out_root,
                    **_model_kwarg(args),
                )
            )
            _write_file(dest / "outputs.md", report)
        else:
            iface, outs = asyncio.run(
                explore(
                    tool_name=args.tool, repo_path=args.repo,
                    package=args.package, out_root=args.out_root,
                    refresh_strategy=args.refresh_strategy,
                    **_model_kwarg(args),
                )
            )
            _write_file(dest / "interface.md", iface)
            _write_file(dest / "outputs.md", outs)

    elif args.command == "author":
        dest = tool_dir(args.package, args.tool, args.out_root)
        iface_path = (
            Path(args.interface_report) if args.interface_report else dest / "interface.md"
        )
        outs_path = (
            Path(args.outputs_report) if args.outputs_report else dest / "outputs.md"
        )
        for p in (iface_path, outs_path):
            if not p.exists():
                parser.error(f"report not found at {p}")
        descriptor = asyncio.run(
            author_boutiques(
                tool_name=args.tool,
                interface_report=iface_path.read_text(encoding="utf-8"),
                output_report=outs_path.read_text(encoding="utf-8"),
                max_retries=args.max_retries,
                **_model_kwarg(args),
            )
        )
        _write_file(dest / f"{args.target}.json", descriptor)

    elif args.command == "wrap":
        dest = asyncio.run(
            wrap(
                tool_name=args.tool, repo_path=args.repo,
                package=args.package, target=args.target,
                out_root=args.out_root,
                refresh_strategy=args.refresh_strategy,
                max_retries=args.max_retries,
                **_model_kwarg(args),
            )
        )
        print(f"Wrote artifacts under {dest}", file=sys.stderr)

    elif args.command == "wrap-all":
        if args.tools:
            tools = [t.strip() for t in args.tools.split(",") if t.strip()]
        else:
            try:
                tools = read_tool_list(args.tools_file)
            except (OSError, ValueError, json.JSONDecodeError) as e:
                parser.error(f"could not read --tools-file {args.tools_file!r}: {e}")
        if not tools:
            parser.error("no tool names found in --tools / --tools-file")
        print(f"Wrapping {len(tools)} tool(s): {', '.join(tools[:8])}"
              f"{' …' if len(tools) > 8 else ''}", file=sys.stderr)
        now = datetime.now(UTC)
        run_id = now.strftime("%Y-%m-%dT%H-%M-%SZ")
        run_root = asyncio.run(
            wrap_all(
                tools=tools, repo_path=args.repo, run_id=run_id,
                package=args.package, target=args.target,
                out_root=args.out_root, max_retries=args.max_retries,
                refresh_strategy=args.refresh_strategy,
                started_at=now.isoformat(),
                **_model_kwarg(args),
            )
        )
        print(f"Run complete: {run_root}", file=sys.stderr)

    else:
        parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
