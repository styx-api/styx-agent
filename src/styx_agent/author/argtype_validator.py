"""Validate argtype documents against the argtype parser.

Unlike the Boutiques path (a hand-rolled Python re-implementation of the Styx-v1
schema in ``validator.py``), argtype has a hand-written grammar whose only
source of truth is its parser. Rather than re-implement and drift, we shell out
to a tiny Node bridge (``tools/argtype_bridge/argtype_validate.mjs``) that runs
``@argtype/core``'s parse-and-resolve passes and returns their errors/warnings
as JSON, with line/column diagnostics we can feed straight back into the
author's retry loop.

This used to compile the document with ``@styx-api/core`` instead, which made
the retry loop a *deployment* gate rather than a grammar one. The distinction is
not academic: a descriptor whose grammar was right but which the compiler could
not lower yet came back as an error, so the loop taught the author to avoid
legal argtype; meanwhile a dangling ``{name}`` in ``.output(...)`` passed,
because lowering does not resolve templates, and surfaced at codegen instead.
The parser catches the second and has no opinion on the first, which is the
right bias for a gate that decides whether a *descriptor* is wrong.

Extra static checks belong in ``@argtype/core`` where they are general, or here
first and upstream once stable — never in a consumer, whose policy would leak
back into the author through this loop. The compiler is still used for the
boutiques path and IR inspection, in ``tools/styx_bridge/``.
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
    """Result of resolving an argtype document via the parser bridge."""

    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: Nodes in the resolved argtype document. Was IR nodes when this compiled;
    #: both are a size, neither is comparable to the other across the change.
    n_nodes: int = 0


def _bridge_path() -> Path:
    override = os.environ.get("STYX_AGENT_ARGTYPE_BRIDGE")
    path = Path(override) if override else _DEFAULT_BRIDGE
    if not path.is_file():
        raise BridgeUnavailable(
            f"argtype validation bridge not found at {path}. Run "
            f"`npm install` in tools/argtype_bridge/ (needs @argtype/core), or set "
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
    """Resolve ``source`` as argtype via the parser bridge; return diagnostics.

    Confirms the document parses and that every resolve pass accepts it —
    including output templates, so a dangling ``{name}`` in a ``.output(...)``
    is an error here rather than a surprise at codegen. It says nothing about
    whether any particular consumer can lower the result, on purpose.

    Raises ``BridgeUnavailable`` for an *environment* failure — Node or the bridge
    script missing, its dependencies not installed/built, or the call timing out —
    so the caller can distinguish a broken toolchain from an invalid descriptor
    (rather than feeding a Node stack trace back to the LLM as a diagnostic).
    A malformed but non-empty bridge response is surfaced as a single error so the
    author loop still gets actionable feedback.
    """
    node = shutil.which("node")
    if node is None:
        raise BridgeUnavailable("`node` not found on PATH; the argtype target requires Node.js.")
    bridge = _bridge_path()

    try:
        proc = subprocess.run(
            [node, str(bridge)],
            input=source,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
    except subprocess.TimeoutExpired as e:
        raise BridgeUnavailable(f"argtype bridge timed out after {e.timeout}s") from e

    out = proc.stdout.strip()
    # The bridge always prints JSON and exits 0 — even on a validation *or* internal
    # error (its own try/catch). Empty stdout with a nonzero exit means Node failed
    # to start the script at all (e.g. `@argtype/core` not installed): an
    # environment failure, not a descriptor defect.
    if not out:
        if proc.returncode != 0:
            raise BridgeUnavailable(
                f"argtype bridge failed to run (exit {proc.returncode}); is @argtype/core "
                f"installed in tools/argtype_bridge? stderr: {proc.stderr.strip()[:400]}"
            )
        return ArgtypeValidation(ok=False, errors=["argtype bridge produced no output"])

    try:
        data = json.loads(out)
        return ArgtypeValidation(
            ok=bool(data.get("ok")),
            errors=[_format_diag(d) for d in data.get("errors", [])],
            warnings=[_format_diag(d) for d in data.get("warnings", [])],
            n_nodes=int(data.get("n_nodes", 0)),
        )
    except (json.JSONDecodeError, TypeError, AttributeError, ValueError) as e:
        return ArgtypeValidation(ok=False, errors=[f"argtype bridge returned a malformed response: {e}: {out[:200]}"])


_bridge_checked = False


def ensure_bridge() -> None:
    """Fail fast (once per process) if the argtype bridge can't run.

    Resolves a trivial document so a broken toolchain raises ``BridgeUnavailable``
    *before* any LLM tokens are spent, instead of surfacing after the first
    completion. Cached, so a ``wrap-all`` campaign pays this at most once.
    """
    global _bridge_checked
    if _bridge_checked:
        return
    validate_argtype("selftest: seq(a: path)")  # raises BridgeUnavailable if broken
    _bridge_checked = True
