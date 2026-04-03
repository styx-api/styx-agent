"""CLI entry point for the Explorer agent.

Usage:
    python -m styx_ai.explorer <tool_name> <repo_path> [--package fsl] [--model ...]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from styx_ai.explorer.agent import explore


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

    parser = argparse.ArgumentParser(description="Explorer agent: extract CLI info from source code")
    parser.add_argument("tool", help="Tool command name (e.g. 'bet', 'bet2', 'flirt')")
    parser.add_argument("repo", help="Path to cloned source repository")
    parser.add_argument("--package", default="fsl", help="Package identifier (default: fsl)")
    parser.add_argument("--model", default=None, help="LLM model override")
    parser.add_argument("--output", "-o", help="Output file path (default: stdout)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Suppress noisy litellm logs unless in verbose mode
    logging.getLogger("LiteLLM").setLevel(logging.WARNING if not args.verbose else logging.DEBUG)
    logging.getLogger("litellm").setLevel(logging.WARNING if not args.verbose else logging.DEBUG)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    kwargs: dict = {"tool_name": args.tool, "repo_path": args.repo, "package": args.package}
    if args.model:
        kwargs["model"] = args.model

    report = asyncio.run(explore(**kwargs))

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report)
            f.write("\n")
        print(f"Wrote report to {args.output}", file=sys.stderr)
    else:
        print(report)


if __name__ == "__main__":
    main()
