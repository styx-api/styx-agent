#!/usr/bin/env python
"""Strip `--help` / `--version` inputs from Boutiques descriptors.

`--help` and `--version` flags print text and exit; they are never functional
inputs, so they're noise in a descriptor. This removes them cleanly — the input
object, its `value-key` token from the (possibly nested) `command-line`, and any
`groups` membership — across a whole tree of `boutiques.json` files.

Generic over any Styx/NiWrap descriptor tree: point it at a styx-agent run dir,
a niwrap package dir, or a single file.

Matching is deliberately conservative to avoid nuking real args:
  - an input matches if its `command-line-flag` is exactly `--help` / `--version`,
    OR its `id` contains `help` / `version` as a whole token (`short_help`,
    `help_flag`, `version` — but NOT `inversion`).
  - bare `-h` / `-v` are NOT matched on the flag alone: `-v` is almost always
    `--verbose`, and ANTs even binds `-h` to verbose on some tools. A `-h`/`-v`
    input is only stripped when its *id* says help/version.

Dry-run by default (prints what it would remove); pass --apply to write.

Usage:
    python scripts/strip_help_version.py <path>                 # dry-run
    python scripts/strip_help_version.py <path> --apply
    python scripts/strip_help_version.py <path> --glob '**/boutiques.json'
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_ID_TOKENS = re.compile(r"[^a-z0-9]+")
_FLAG_MATCH = {"--help", "--version"}


def is_help_version(inp: dict) -> bool:
    """True if this input is a --help / --version flag (conservative)."""
    if not isinstance(inp, dict):
        return False
    if inp.get("command-line-flag") in _FLAG_MATCH:
        return True
    tokens = _ID_TOKENS.split(str(inp.get("id", "")).lower())
    return "help" in tokens or "version" in tokens


def strip_node(node: object, removed: list[str]) -> None:
    """Recursively remove help/version inputs and scrub their references.

    Each descriptor level that owns an `inputs` list also owns the `command-line`
    template and `groups` that reference those inputs' value-keys, so we scrub
    them together at that level, then recurse into the surviving (sub)inputs.
    """
    if isinstance(node, list):
        for item in node:
            strip_node(item, removed)
        return
    if not isinstance(node, dict):
        return

    inputs = node.get("inputs")
    if isinstance(inputs, list):
        kept, dropped_keys, dropped_ids = [], [], []
        for inp in inputs:
            if is_help_version(inp):
                removed.append(inp.get("id", "<no-id>"))
                dropped_ids.append(inp.get("id"))
                if inp.get("value-key"):
                    dropped_keys.append(inp["value-key"])
            else:
                kept.append(inp)
        node["inputs"] = kept

        cl = node.get("command-line")
        if isinstance(cl, str) and dropped_keys:
            tokens = [t for t in cl.split() if t not in dropped_keys]
            node["command-line"] = " ".join(tokens)

        groups = node.get("groups")
        if isinstance(groups, list) and dropped_ids:
            for g in groups:
                if isinstance(g.get("members"), list):
                    g["members"] = [m for m in g["members"] if m not in dropped_ids]
            node["groups"] = [g for g in groups if g.get("members")]

    # Recurse into every value (surviving subcommand inputs live under here).
    for value in node.values():
        strip_node(value, removed)


def main() -> None:
    ap = argparse.ArgumentParser(description="Strip --help/--version inputs from Boutiques descriptors")
    ap.add_argument("path", help="Descriptor file, or a directory tree to scan")
    ap.add_argument("--glob", default="**/boutiques.json", help="Glob for descriptors under a dir (default: **/boutiques.json)")
    ap.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    args = ap.parse_args()

    root = Path(args.path)
    if root.is_file():
        files = [root]
    else:
        files = sorted(root.glob(args.glob))
    if not files:
        print(f"[strip] no descriptors found under {root}", file=sys.stderr)
        sys.exit(1)

    total_removed = 0
    touched = 0
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"[skip] {f}: {e}", file=sys.stderr)
            continue
        removed: list[str] = []
        strip_node(d, removed)
        if removed:
            touched += 1
            total_removed += len(removed)
            tool = f.parent.name
            print(f"  {tool:36} - {', '.join(removed)}")
            if args.apply:
                f.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")

    verb = "removed" if args.apply else "would remove"
    print(f"\n[strip] {verb} {total_removed} help/version input(s) across {touched}/{len(files)} descriptors"
          + ("" if args.apply else "  (dry-run; pass --apply to write)"))


if __name__ == "__main__":
    main()
