"""Resolve a ``--tools-file`` argument into a de-duplicated list of tool names.

Accepts whatever produced the list, so styx-agent never has to run a container:

- a newline-delimited text file (blank lines and ``#`` comments ignored);
- a JSON file holding an array of names, an array of descriptor objects, a
  single descriptor object (the executable name is the ``name`` field), or a
  NiWrap version manifest ``src/niwrap/<pkg>/<ver>/version.json`` (its ``apps``
  array is the per-descriptor tool list); or
- a directory of NiWrap descriptors — one ``<tool>.json`` per tool, each with a
  ``name`` field (e.g. ``niwrap/dist/pages/<ver>/descriptors/<package>/``).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path


def read_tool_list(path: str | Path) -> list[str]:
    """Return tool names from a text/JSON file or a NiWrap descriptors directory."""
    p = Path(path)
    if p.is_dir():
        return _dedupe(_names_from_descriptor_dir(p))
    text = p.read_text(encoding="utf-8")
    if p.suffix == ".json" or text.lstrip().startswith(("[", "{")):
        return _dedupe(_names_from_json(json.loads(text)))
    return _dedupe(
        s for line in text.splitlines() if (s := line.strip()) and not s.startswith("#")
    )


def _names_from_descriptor_dir(d: Path) -> list[str]:
    names: list[str] = []
    for f in sorted(d.glob("*.json")):
        name = None
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = None
        if isinstance(data, dict) and isinstance(data.get("name"), str):
            name = data["name"]
        names.append(name or f.stem)  # fall back to the filename
    return names


def _names_from_json(data: object) -> list[str]:
    if isinstance(data, dict):
        # NiWrap per-version manifest, e.g. src/niwrap/<pkg>/<ver>/version.json:
        # {"name": "<version>", "apps": [tool, ...], "executables": {...}}.
        # 'apps' is the per-descriptor tool list (one wrap each — including
        # subcommands, e.g. one `wb_command` executable exposes many apps);
        # 'executables' is the underlying binaries. Check 'apps' FIRST — the
        # top-level 'name' here is the version, not a tool.
        apps = data.get("apps")
        if isinstance(apps, list):
            return [a for a in apps if isinstance(a, str)]
        # Otherwise a single descriptor object whose 'name' is the executable.
        if isinstance(data.get("name"), str):
            return [data["name"]]
        raise ValueError(
            "unrecognized JSON object: expected a NiWrap version manifest (with "
            "'apps') or a descriptor object (with 'name')"
        )
    if isinstance(data, list):
        out: list[str] = []
        for item in data:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict) and isinstance(item.get("name"), str):
                out.append(item["name"])
        return out
    raise ValueError(
        "unrecognized JSON tool-list shape: expected an array of names, an array "
        "of descriptor objects, a descriptor, or a NiWrap version manifest"
    )


def _dedupe(names: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out
