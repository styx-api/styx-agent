#!/usr/bin/env python
"""Clone upstream source repos listed in repos.json into ./repos/<package>/.

Single-URL packages land at ``repos/<package>``. Multi-URL packages (e.g.
FSL, where each tool is a separate repo) land at ``repos/<package>/<repo>``.
Idempotent: existing destinations are skipped.

Usage:
    python scripts/clone_repos.py             # clone every package
    python scripts/clone_repos.py --package afni
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Clone styx-agent source repos")
    parser.add_argument("--package", help="Only clone this package (default: all)")
    parser.add_argument("--repos-dir", default="repos", help="Destination root (default: repos)")
    parser.add_argument("--registry", default="scripts/repos.json", help="Registry JSON path")
    args = parser.parse_args()

    registry = json.loads((ROOT / args.registry).read_text(encoding="utf-8"))
    repos_dir = ROOT / args.repos_dir
    repos_dir.mkdir(exist_ok=True)

    packages = [args.package] if args.package else list(registry)
    exit_code = 0
    for pkg in packages:
        if pkg not in registry:
            print(f"[err] unknown package: {pkg}", file=sys.stderr)
            exit_code = 1
            continue
        urls = registry[pkg]
        if not urls:
            print(f"[skip] {pkg}: no URLs in registry")
            continue
        for url in urls:
            dest = (
                repos_dir / pkg
                if len(urls) == 1
                else repos_dir / pkg / url.rsplit("/", 1)[-1].removesuffix(".git")
            )
            if dest.exists():
                print(f"[skip] {dest.relative_to(ROOT)} already exists")
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            print(f"[clone] {url} -> {dest.relative_to(ROOT)}")
            result = subprocess.run(["git", "clone", "--depth", "1", url, str(dest)])
            if result.returncode != 0:
                print(f"[err] clone failed for {url}", file=sys.stderr)
                exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
