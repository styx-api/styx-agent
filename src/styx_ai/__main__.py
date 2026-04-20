"""Top-level CLI for styx-ai.

Subcommands:

    styx-ai scan <repo>                           # per-package strategy scan
    styx-ai explore <tool> <repo>                 # per-tool exploration (strategy + interface + outputs)
    styx-ai explore <tool> <repo> --interface-only
    styx-ai explore <tool> <repo> --outputs-only --interface-report report.md
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from styx_ai.explorer import explore, explore_interface, explore_outputs
from styx_ai.scanner import explore_strategy


def _configure_stdout() -> None:
    """Ensure stdout can emit UTF-8 (Windows consoles default to cp1252)."""
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


def _write_result(result: str, output_path: str | None) -> None:
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(result)
            f.write("\n")
        print(f"Wrote report to {output_path}", file=sys.stderr)
    else:
        print(result)


def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--package", default="fsl", help="Package identifier (default: fsl)")
    p.add_argument("--model", default=None, help="LLM model override")
    p.add_argument("--output", "-o", help="Output file path (default: stdout)")
    p.add_argument("--cache-dir", help="Strategy cache directory (default: output/_strategies)")
    p.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")


def _common_kwargs(args: argparse.Namespace) -> dict:
    kwargs: dict = {
        "repo_path": args.repo,
        "package": args.package,
        "cache_dir": args.cache_dir,
    }
    if args.model:
        kwargs["model"] = args.model
    return kwargs


def main() -> None:
    _configure_stdout()
    _load_dotenv()

    parser = argparse.ArgumentParser(prog="styx-ai", description="Styx AI tooling")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # styx-ai scan <repo>
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

    # styx-ai explore <tool> <repo>
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
        help="Only trace outputs (requires --interface-report)",
    )
    explore_p.add_argument(
        "--interface-report",
        help="Path to interface report (for --outputs-only mode)",
    )
    _add_common_args(explore_p)

    args = parser.parse_args()
    _configure_logging(args.verbose)

    if args.command == "scan":
        result = asyncio.run(
            explore_strategy(refresh=args.refresh, **_common_kwargs(args))
        )
    elif args.command == "explore":
        if args.outputs_only and not args.interface_report:
            parser.error("--outputs-only requires --interface-report")
        common = _common_kwargs(args)
        if args.interface_only:
            result = asyncio.run(explore_interface(tool_name=args.tool, **common))
        elif args.outputs_only:
            with open(args.interface_report, encoding="utf-8") as f:
                interface_report = f.read()
            result = asyncio.run(
                explore_outputs(
                    tool_name=args.tool, interface_report=interface_report, **common
                )
            )
        else:
            result = asyncio.run(
                explore(
                    tool_name=args.tool,
                    refresh_strategy=args.refresh_strategy,
                    **common,
                )
            )
    else:
        parser.error(f"unknown command: {args.command}")

    _write_result(result, args.output)


if __name__ == "__main__":
    main()
