"""CLI entry point for the Explorer agents.

Usage:
    # Run both agents (interface + outputs)
    python -m styx_ai.explorer <tool_name> <repo_path>

    # Run only the interface agent
    python -m styx_ai.explorer <tool_name> <repo_path> --interface-only

    # Run only the output agent (requires --interface-report)
    python -m styx_ai.explorer <tool_name> <repo_path> --outputs-only --interface-report report.md
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from styx_ai.explorer.agent import explore, explore_interface, explore_outputs


def _load_dotenv() -> None:
    """Load .env file from CWD if it exists."""
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


def main() -> None:
    _load_dotenv()

    parser = argparse.ArgumentParser(description="Explorer agents for CLI tool analysis")
    parser.add_argument("tool", help="Tool command name (e.g. 'bet', 'bet2', '3dTstat')")
    parser.add_argument("repo", help="Path to cloned source repository")
    parser.add_argument("--package", default="fsl", help="Package identifier (default: fsl)")
    parser.add_argument("--model", default=None, help="LLM model override")
    parser.add_argument("--output", "-o", help="Output file path (default: stdout)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--interface-only", action="store_true",
        help="Only extract input interface (skip output tracing)",
    )
    mode.add_argument(
        "--outputs-only", action="store_true",
        help="Only trace outputs (requires --interface-report)",
    )
    parser.add_argument(
        "--interface-report",
        help="Path to interface report (for --outputs-only mode)",
    )

    args = parser.parse_args()

    if args.outputs_only and not args.interface_report:
        parser.error("--outputs-only requires --interface-report")

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("LiteLLM").setLevel(logging.WARNING if not args.verbose else logging.DEBUG)
    logging.getLogger("litellm").setLevel(logging.WARNING if not args.verbose else logging.DEBUG)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    kwargs: dict = {"tool_name": args.tool, "repo_path": args.repo, "package": args.package}
    if args.model:
        kwargs["model"] = args.model

    if args.interface_only:
        result = asyncio.run(explore_interface(**kwargs))
    elif args.outputs_only:
        with open(args.interface_report, encoding="utf-8") as f:
            interface_report = f.read()
        result = asyncio.run(explore_outputs(interface_report=interface_report, **kwargs))
    else:
        result = asyncio.run(explore(**kwargs))

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result)
            f.write("\n")
        print(f"Wrote report to {args.output}", file=sys.stderr)
    else:
        print(result)


if __name__ == "__main__":
    main()
