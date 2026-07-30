"""Validate argtype documents with the real styx compiler.

Unlike the Boutiques path (a hand-rolled Python re-implementation of the Styx-v1
schema in ``validator.py``), argtype has a hand-written grammar whose only
source of truth is the styx (v2) compiler frontend. Rather than re-implement and drift, we
shell out to a tiny Node bridge (``tools/argtype_bridge/argtype_validate.mjs``)
that calls ``@styx-api/core``'s ``compile(src, {format: "argtype"})`` and returns
its errors/warnings as JSON. This is strictly stronger than a schema check: it
confirms the document parses *and* lowers to the Styx IR, with line/column
diagnostics we can feed straight back into the author's retry loop.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# tools/argtype_bridge/argtype_validate.mjs lives at the repo root; this file is
# src/styx_agent/author/argtype_validator.py → parents[3] is the repo root.
_DEFAULT_BRIDGE = Path(__file__).resolve().parents[3] / "tools" / "argtype_bridge" / "argtype_validate.mjs"


class BridgeUnavailable(RuntimeError):
    """The Node argtype bridge could not be located or executed."""


@dataclass
class ArgtypeValidation:
    """Result of compiling an argtype document via the styx bridge."""

    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    n_nodes: int = 0


def _bridge_path() -> Path:
    override = os.environ.get("STYX_AGENT_ARGTYPE_BRIDGE")
    path = Path(override) if override else _DEFAULT_BRIDGE
    if not path.is_file():
        raise BridgeUnavailable(
            f"argtype validation bridge not found at {path}. Run "
            f"`npm install` in tools/argtype_bridge/ (needs @styx-api/core), or set "
            f"STYX_AGENT_ARGTYPE_BRIDGE to the argtype_validate.mjs path."
        )
    return path


def _format_diag(d: dict) -> str:
    msg = d.get("message", "")
    line, col = d.get("line"), d.get("column")
    if line is not None and col is not None:
        return f"{line}:{col}: {msg}"
    if line is not None:
        return f"line {line}: {msg}"
    return msg


def validate_argtype(source: str) -> ArgtypeValidation:
    """Compile ``source`` as argtype via the styx bridge; return diagnostics.

    Raises ``BridgeUnavailable`` if Node or the bridge script is missing (a hard
    dependency of the argtype target). A malformed bridge response is surfaced as
    a single error so the author loop still gets actionable feedback.
    """
    node = shutil.which("node")
    if node is None:
        raise BridgeUnavailable("`node` not found on PATH; the argtype target requires Node.js.")
    bridge = _bridge_path()

    proc = subprocess.run(
        [node, str(bridge)],
        input=source,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    out = proc.stdout.strip()
    if not out:
        return ArgtypeValidation(
            ok=False,
            errors=[f"argtype bridge produced no output (exit {proc.returncode}): {proc.stderr.strip()}"],
        )
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        return ArgtypeValidation(ok=False, errors=[f"argtype bridge returned non-JSON: {e}: {out[:200]}"])

    return ArgtypeValidation(
        ok=bool(data.get("ok")),
        errors=[_format_diag(d) for d in data.get("errors", [])],
        warnings=[_format_diag(d) for d in data.get("warnings", [])],
        n_nodes=int(data.get("n_nodes", 0)),
    )
