#!/usr/bin/env python
"""Stage generated boutiques descriptors into the niwrap catalog for a PR.

Takes a styx-agent run dir (the per-tool `<tool>/boutiques.json` outputs) and
copies each descriptor into the matching niwrap tool dir, applying the
transforms niwrap expects:

  - strip `media-types` (niwrap descriptors don't carry them yet),
  - reorder top-level keys to niwrap's convention (clean diffs),
  - write 2-space JSON with a trailing newline (niwrap's format).

It also updates each tool's `app.json` (the per-tool source manifest that the
compiler reads): sets `source` to `{type: "boutiques", path: "boutiques.json"}`
— required for the tools that shipped as bare `{name}` stubs with no descriptor
source — and sets `docs.description` from the descriptor, preserving any
existing `docs.authors`.

It does NOT touch `version.json`: for ANTs 2.5.3 the `apps` list and
`executables` already cover all 113 tools. The script verifies each tool is
present in the target `version.json` `apps` and warns on any that isn't (those
would need a manifest entry before compiling).

Dry-run by default; pass --apply to write.

Usage:
    python scripts/stage_to_niwrap.py <run-dir>/ants \
        --niwrap ../niwrap/src/niwrap/ants/2.5.3
    python scripts/stage_to_niwrap.py <run-dir>/ants --niwrap <path> --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# niwrap top-level key convention (present keys emitted in this order, then rest)
_KEY_ORDER = ["name", "command-line", "author", "description", "schema-version",
              "inputs", "output-files", "url"]


def strip_media_types(node: object) -> int:
    """Recursively delete every `media-types` key. Returns count removed."""
    removed = 0
    if isinstance(node, dict):
        if "media-types" in node:
            del node["media-types"]
            removed += 1
        for v in node.values():
            removed += strip_media_types(v)
    elif isinstance(node, list):
        for v in node:
            removed += strip_media_types(v)
    return removed


def reorder(d: dict) -> dict:
    ordered = {k: d[k] for k in _KEY_ORDER if k in d}
    for k, v in d.items():
        if k not in ordered:
            ordered[k] = v
    return ordered


def update_app_json(app_path: Path, description: str) -> tuple[dict, list[str]]:
    """Return (updated app.json dict, list of change tags) — source + docs.description."""
    app = json.loads(app_path.read_text(encoding="utf-8")) if app_path.exists() else {"name": app_path.parent.name}
    changes: list[str] = []
    src = {"type": "boutiques", "path": "boutiques.json"}
    if app.get("source") != src:
        app["source"] = src
        changes.append("source")
    docs = app.get("docs") or {}
    if description and docs.get("description") != description:
        changes.append("docs+" if "description" not in docs else "docs~")
        docs["description"] = description
    if docs:
        app["docs"] = docs
    # niwrap key order: name, source, docs
    ordered = {k: app[k] for k in ("name", "exe", "args", "source", "docs") if k in app}
    for k, v in app.items():
        ordered.setdefault(k, v)
    return ordered, changes


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage generated descriptors into the niwrap catalog")
    ap.add_argument("source", help="Run-dir package folder holding <tool>/boutiques.json")
    ap.add_argument("--niwrap", required=True, help="Target niwrap version dir (e.g. ../niwrap/src/niwrap/ants/2.5.3)")
    ap.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    args = ap.parse_args()

    source = Path(args.source)
    target = Path(args.niwrap)
    src_files = sorted(source.glob("*/boutiques.json"))
    if not src_files:
        print(f"[stage] no descriptors under {source}", file=sys.stderr)
        sys.exit(1)

    # manifest coverage check (informational — we don't edit manifests)
    apps: set[str] = set()
    ver = target / "version.json"
    if ver.exists():
        apps = set(json.loads(ver.read_text(encoding="utf-8")).get("apps", []))

    replaced = added = stripped = not_in_manifest = src_added = docs_added = docs_changed = 0
    for f in src_files:
        tool = f.parent.name
        d = json.loads(f.read_text(encoding="utf-8"))
        description = d.get("description") or ""
        stripped += strip_media_types(d)
        d = reorder(d)

        dest_dir = target / tool
        dest = dest_dir / "boutiques.json"
        action = "replace" if dest.exists() else "ADD"
        if action == "ADD":
            added += 1
        else:
            replaced += 1
        flag = "" if tool in apps else "  [NOT IN version.json apps]"
        if flag:
            not_in_manifest += 1

        app, changes = update_app_json(dest_dir / "app.json", description)
        if "source" in changes:
            src_added += 1
        if "docs+" in changes:
            docs_added += 1
        if "docs~" in changes:
            docs_changed += 1
        note = f"  app.json: {', '.join(changes)}" if changes else ""
        print(f"  {action:7} {tool}{flag}{note}")

        if args.apply:
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")
            (dest_dir / "app.json").write_text(json.dumps(app, indent=2) + "\n", encoding="utf-8")

    verb = "staged" if args.apply else "would stage"
    print(f"\n[stage] {verb} {len(src_files)} descriptors -> {target}")
    print(f"        boutiques.json: {replaced} replace, {added} add | media-types stripped: {stripped}")
    print(f"        app.json: {src_added} source added, {docs_added} docs added, {docs_changed} docs updated")
    if not_in_manifest:
        print(f"        WARNING: {not_in_manifest} tool(s) not in version.json apps — add a manifest entry before compiling")
    if not args.apply:
        print("        (dry-run; pass --apply to write)")


if __name__ == "__main__":
    main()
