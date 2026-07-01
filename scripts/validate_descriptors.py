#!/usr/bin/env python
"""Validate a tree of Boutiques descriptors as a pre-PR gate.

Runs several independent gates over every `boutiques.json` under a root and
prints a PASS/FAIL summary. Exits non-zero if any gate fails, so it can wire
straight into CI.

Gates:
  1. leaks       - no LLM tool-call tokens leaked into descriptors or reports
                   (kimi's `<|tool_call...|>` / `tool_calls_section`).
  2. structure   - command-line/value-key integrity: every value-key used in a
                   `command-line` is declared by an input at that level (no
                   dangling refs — catches a bad strip/edit), every declared
                   input has an id+type, and every `output-files` entry has a
                   `path-template`.
  3. citations   - (optional) every `<!-- source: file:line-line -->` in the
                   sibling interface.md/outputs.md resolves to a real, in-bounds,
                   non-empty span in the source repo. Needs --source.

Usage:
    python scripts/validate_descriptors.py <root>
    python scripts/validate_descriptors.py <root> --source repos/ants
    python scripts/validate_descriptors.py <root> --glob '*/boutiques.json'
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_LEAK = re.compile(r"tool_call_begin|tool_calls_section|<\|tool_call")
_VALUE_KEY = re.compile(r"\[[A-Za-z0-9_]+\]")
_SOURCE_REF = re.compile(r"source:\s*([^\s:]+):(\d+)-(\d+)")
_ID_TOKENS = re.compile(r"[^a-z0-9]+")


def is_meta_flag(inp: dict) -> bool:
    """True if this input is a --help / --version meta-flag (matches the stripper)."""
    if not isinstance(inp, dict):
        return False
    if inp.get("command-line-flag") in ("--help", "--version"):
        return True
    tokens = _ID_TOKENS.split(str(inp.get("id", "")).lower())
    return "help" in tokens or "version" in tokens


def walk_command_contexts(node):
    """Yield every dict that owns both a `command-line` string and `inputs` list."""
    if isinstance(node, dict):
        if isinstance(node.get("command-line"), str) and isinstance(node.get("inputs"), list):
            yield node
        for v in node.values():
            yield from walk_command_contexts(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk_command_contexts(v)


def iter_inputs(node):
    """Yield every input object anywhere in the descriptor."""
    if isinstance(node, dict):
        if isinstance(node.get("inputs"), list):
            yield from node["inputs"]
        for v in node.values():
            yield from iter_inputs(v)
    elif isinstance(node, list):
        for v in node:
            yield from iter_inputs(v)


def check_structure(d: dict) -> list[str]:
    errs: list[str] = []
    # value-key / command-line integrity, per command context
    for ctx in walk_command_contexts(d):
        used = set(_VALUE_KEY.findall(ctx["command-line"]))
        declared = {i.get("value-key") for i in ctx["inputs"] if isinstance(i, dict) and i.get("value-key")}
        for dangling in sorted(used - declared):
            errs.append(f"dangling value-key {dangling} in command-line with no matching input")
    # every input has id + type
    for inp in iter_inputs(d):
        if not isinstance(inp, dict):
            continue
        if not inp.get("id"):
            errs.append("input with no id")
        elif not inp.get("type"):
            errs.append(f"input '{inp['id']}' has no type")
    # output-files have a path-template
    def check_outputs(node):
        if isinstance(node, dict):
            if node.get("id") and "path-template" in node and not node["path-template"]:
                errs.append(f"output '{node['id']}' has empty path-template")
            for of in (node.get("output-files") or []):
                if isinstance(of, dict) and not of.get("path-template"):
                    errs.append(f"output-file '{of.get('id','?')}' missing path-template")
            for v in node.values():
                check_outputs(v)
        elif isinstance(node, list):
            for v in node:
                check_outputs(v)
    check_outputs(d)
    return errs


def check_citations(tool_dir: Path, source_root: Path, cache: dict) -> tuple[int, int, list[str]]:
    total = valid = 0
    bad: list[str] = []
    for md in ("interface.md", "outputs.md"):
        p = tool_dir / md
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for path, a, b in _SOURCE_REF.findall(text):
            total += 1
            a, b = int(a), int(b)
            fp = source_root / path
            key = str(fp)
            if key not in cache:
                cache[key] = fp.read_text(encoding="utf-8", errors="replace").splitlines() if fp.exists() else None
            lines = cache[key]
            ok = lines is not None and 1 <= a <= len(lines) and b <= len(lines) + 2 and any(s.strip() for s in lines[a - 1:b])
            if ok:
                valid += 1
            else:
                bad.append(f"{md} -> {path}:{a}-{b}")
    return total, valid, bad


def main() -> None:
    ap = argparse.ArgumentParser(description="Validate a tree of Boutiques descriptors")
    ap.add_argument("root", help="Directory tree containing descriptors")
    ap.add_argument("--glob", default="**/boutiques.json", help="Glob for descriptors (default: **/boutiques.json)")
    ap.add_argument("--source", help="Source repo root to validate `source:` citations against (enables gate 3)")
    args = ap.parse_args()

    root = Path(args.root)
    files = sorted(root.glob(args.glob))
    if not files:
        print(f"[validate] no descriptors under {root}", file=sys.stderr)
        sys.exit(2)

    leak_hits: list[str] = []
    struct_fail: dict[str, list[str]] = {}
    meta_flag_hits: dict[str, list[str]] = {}
    cite_total = cite_valid = 0
    cite_bad: list[str] = []
    src_cache: dict = {}
    source_root = Path(args.source) if args.source else None

    for f in files:
        tool = f.parent.name
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            struct_fail[tool] = [f"unreadable: {e}"]
            continue
        # 1. leaks (descriptor + sibling reports)
        for probe in [f, f.parent / "interface.md", f.parent / "outputs.md"]:
            if probe.exists() and _LEAK.search(probe.read_text(encoding="utf-8", errors="replace")):
                leak_hits.append(f"{tool}/{probe.name}")
        # 2. structure
        errs = check_structure(d)
        if errs:
            struct_fail[tool] = errs
        # meta-flag warning (non-failing): --help/--version left in the descriptor
        metas = [i.get("id", "?") for i in iter_inputs(d) if is_meta_flag(i)]
        if metas:
            meta_flag_hits[tool] = metas
        # 3. citations
        if source_root:
            t, v, bad = check_citations(f.parent, source_root, src_cache)
            cite_total += t
            cite_valid += v
            cite_bad += [f"{tool}: {x}" for x in bad]

    n = len(files)
    print(f"Validated {n} descriptors under {root}\n")

    ok = True
    # gate 1
    if leak_hits:
        ok = False
        print(f"FAIL  leaks: {len(leak_hits)} file(s) contain tool-call tokens")
        for h in leak_hits[:10]:
            print(f"        {h}")
    else:
        print("PASS  leaks: none")
    # gate 2
    if struct_fail:
        ok = False
        n_err = sum(len(v) for v in struct_fail.values())
        print(f"FAIL  structure: {n_err} issue(s) in {len(struct_fail)} descriptor(s)")
        for tool, errs in list(struct_fail.items())[:10]:
            print(f"        {tool}: {errs[0]}" + (f"  (+{len(errs)-1} more)" if len(errs) > 1 else ""))
    else:
        print("PASS  structure: all command-line/value-key refs resolve; inputs typed; outputs templated")
    # gate 3
    if source_root:
        pct = 100 * cite_valid // cite_total if cite_total else 100
        if cite_bad:
            ok = False
            print(f"FAIL  citations: {cite_valid}/{cite_total} valid ({pct}%) — {len(cite_bad)} bad")
            for b in cite_bad[:10]:
                print(f"        {b}")
        else:
            print(f"PASS  citations: {cite_valid}/{cite_total} valid (100%)")
    else:
        print("SKIP  citations: pass --source <repo> to enable")

    # non-failing warning: help/version meta-flags still present
    if meta_flag_hits:
        n_meta = sum(len(v) for v in meta_flag_hits.values())
        print(f"WARN  meta-flags: {n_meta} --help/--version input(s) in {len(meta_flag_hits)} descriptor(s)"
              f" — run scripts/strip_help_version.py")
        for tool, metas in list(meta_flag_hits.items())[:10]:
            print(f"        {tool}: {', '.join(metas)}")

    print()
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
