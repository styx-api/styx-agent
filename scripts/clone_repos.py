#!/usr/bin/env python
"""Clone upstream source repos for a NiWrap package version, pinned to match the build.

The source registry now lives in the NiWrap metadata manifests, not in this repo.
Each ``version.json`` carries a ``sources`` array of pinned repositories:

    "sources": [
      {"repo": "https://github.com/ANTsX/ANTs.git", "ref": "v2.5.3", "role": "primary"},
      {"repo": "https://github.com/InsightSoftwareConsortium/ITK.git",
       "ref": "4535548a...", "role": "dependency", "note": "..."}
    ]

This script reads those manifests and clones each source at its pinned ``ref``
(tag, branch, or commit SHA; ``null`` = default branch).

Layout under ``--repos-dir`` (default ``repos/``):
    repos/<package>                      single primary source
    repos/<package>/<repo>               multiple primary sources (e.g. FSL)
    repos/<package>/_deps/<repo>         dependency sources (e.g. ITK for ANTs)

Idempotent: existing destinations are skipped.

Usage:
    python scripts/clone_repos.py                      # every package, default version
    python scripts/clone_repos.py --package ants       # one package
    python scripts/clone_repos.py --package fsl --version 6.0.4
    python scripts/clone_repos.py --primary-only       # skip dependency repos
    python scripts/clone_repos.py --niwrap ../niwrap/src/niwrap
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# NiWrap manifests live as a sibling checkout in the styxniwrap workspace.
DEFAULT_NIWRAP = ROOT.parent / "niwrap" / "src" / "niwrap"

_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def repo_name(url: str) -> str:
    return url.rsplit("/", 1)[-1].removesuffix(".git")


def load_sources(niwrap: Path, package: str, version: str | None) -> tuple[str, list[dict]]:
    """Return (version, sources) for a package, defaulting to its ``default`` version."""
    pkg_json = niwrap / package / "package.json"
    if not pkg_json.exists():
        raise FileNotFoundError(f"no package manifest: {pkg_json}")
    pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
    version = version or pkg.get("default")
    if not version:
        raise ValueError(f"{package}: no version given and no 'default' in package.json")
    ver_json = niwrap / package / version / "version.json"
    if not ver_json.exists():
        raise FileNotFoundError(f"no version manifest: {ver_json}")
    sources = json.loads(ver_json.read_text(encoding="utf-8")).get("sources", [])
    return version, sources


def clone_pinned(url: str, ref: str | None, dest: Path) -> bool:
    """Clone ``url`` at ``ref`` into ``dest``. Returns True on success."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if ref is None:
        cmd = ["git", "clone", "--depth", "1", url, str(dest)]
    elif _SHA_RE.match(ref):
        # Arbitrary commit: a shallow clone can't --branch a SHA. Fetch it directly.
        # (GitHub honors fetch-by-SHA; FSL refs are all tags/branches, handled below.)
        dest.mkdir(parents=True, exist_ok=True)
        steps = [
            ["git", "-C", str(dest), "init", "-q"],
            ["git", "-C", str(dest), "remote", "add", "origin", url],
            ["git", "-C", str(dest), "fetch", "--depth", "1", "origin", ref],
            ["git", "-C", str(dest), "checkout", "-q", "FETCH_HEAD"],
        ]
        for step in steps:
            if subprocess.run(step).returncode != 0:
                print(f"[err] {' '.join(step)} failed", file=sys.stderr)
                return False
        return True
    else:
        cmd = ["git", "clone", "--depth", "1", "--branch", ref, url, str(dest)]
    return subprocess.run(cmd).returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Clone NiWrap source repos, pinned to the build")
    parser.add_argument("--package", help="Only this package (default: all in project.json)")
    parser.add_argument("--version", help="Version to clone (default: package 'default')")
    parser.add_argument("--niwrap", default=str(DEFAULT_NIWRAP), help="Path to niwrap src/niwrap")
    parser.add_argument("--repos-dir", default="repos", help="Destination root (default: repos)")
    parser.add_argument("--primary-only", action="store_true", help="Skip dependency sources")
    args = parser.parse_args()

    niwrap = Path(args.niwrap).resolve()
    if not niwrap.exists():
        print(f"[err] niwrap manifests not found: {niwrap}\n"
              f"      pass --niwrap <path to niwrap/src/niwrap>", file=sys.stderr)
        sys.exit(2)

    if args.package:
        packages = [args.package]
    else:
        project = json.loads((niwrap / "project.json").read_text(encoding="utf-8"))
        packages = project["packages"]

    repos_dir = ROOT / args.repos_dir
    exit_code = 0
    for pkg in packages:
        try:
            version, sources = load_sources(niwrap, pkg, args.version)
        except (FileNotFoundError, ValueError) as e:
            print(f"[err] {pkg}: {e}", file=sys.stderr)
            exit_code = 1
            continue
        if not sources:
            print(f"[skip] {pkg} {version}: no sources in manifest")
            continue

        primaries = [s for s in sources if s.get("role") == "primary"]
        for s in sources:
            role = s.get("role", "primary")
            if role == "dependency" and args.primary_only:
                continue
            url, ref = s["repo"], s.get("ref")
            if role == "dependency":
                dest = repos_dir / pkg / "_deps" / repo_name(url)
            elif len(primaries) == 1:
                dest = repos_dir / pkg
            else:
                dest = repos_dir / pkg / repo_name(url)

            if dest.exists():
                print(f"[skip] {dest.relative_to(ROOT)} already exists")
                continue
            pin = ref if ref else "(default branch)"
            print(f"[clone] {url} @ {pin} -> {dest.relative_to(ROOT)}")
            if not clone_pinned(url, ref, dest):
                print(f"[err] clone failed for {url} @ {pin}", file=sys.stderr)
                exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
