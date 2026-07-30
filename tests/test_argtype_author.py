"""Tests for the argtype author: fence stripping, target dispatch, the retry
loop (LLM + validator mocked), and end-to-end compiler validation (skipped when
the Node bridge is unavailable, e.g. in CI without a local styx build)."""

import asyncio
import shutil

import pytest

from styx_agent.author import TARGETS
from styx_agent.author import argtype as argtype_mod
from styx_agent.author.argtype import _strip_fences, author_argtype
from styx_agent.author.argtype_validator import ArgtypeValidation, validate_argtype

# --- _strip_fences -------------------------------------------------------

def test_strip_fences_plain_passthrough():
    src = "bet: seq(a: path)"
    assert _strip_fences(src) == src


def test_strip_fences_removes_labeled_and_bare_fence():
    assert _strip_fences("```argtype\nbet: seq(a: path)\n```") == "bet: seq(a: path)"
    assert _strip_fences("```\nbet: seq(a: path)\n```") == "bet: seq(a: path)"


def test_strip_fences_preserves_inner_backtick_templates():
    # argtype output templates use backticks; a fence stripper must NOT touch them.
    src = "bet: seq(a: path).output(o: `{a}.nii.gz`)"
    assert _strip_fences(src) == src
    assert _strip_fences("```argtype\n" + src + "\n```") == src


def test_strip_fences_handles_surrounding_whitespace():
    assert _strip_fences("\n\n```\nbet: seq(a: path)\n```\n") == "bet: seq(a: path)"


# --- TARGETS registry ----------------------------------------------------

def test_targets_registry_maps_names_to_fn_and_extension():
    assert set(TARGETS) == {"boutiques", "argtype"}
    assert TARGETS["boutiques"][1] == "json"
    assert TARGETS["argtype"][1] == "argtype"
    for fn, ext in TARGETS.values():
        assert callable(fn) and isinstance(ext, str)


# --- retry loop (LLM + validator mocked) ---------------------------------

def test_author_retries_on_invalid_then_succeeds(monkeypatch):
    calls = {"n": 0}

    async def fake_complete(messages, model):
        calls["n"] += 1
        return ("BAD" if calls["n"] == 1 else "GOOD"), 10, 5

    monkeypatch.setattr(argtype_mod, "_complete", fake_complete)
    monkeypatch.setattr(
        argtype_mod, "validate_argtype",
        lambda src: ArgtypeValidation(ok=(src == "GOOD"), errors=[] if src == "GOOD" else ["boom"]),
    )
    out = asyncio.run(author_argtype("t", "iface", "outs", max_retries=3))
    assert out == "GOOD" and calls["n"] == 2


def test_author_raises_after_max_retries(monkeypatch):
    async def fake_complete(messages, model):
        return "BAD", 10, 5

    monkeypatch.setattr(argtype_mod, "_complete", fake_complete)
    monkeypatch.setattr(
        argtype_mod, "validate_argtype",
        lambda src: ArgtypeValidation(ok=False, errors=["nope"]),
    )
    with pytest.raises(ValueError):
        asyncio.run(author_argtype("t", "iface", "outs", max_retries=1))


# --- bridge integration (skipped when Node/bridge unavailable) -----------

def _bridge_ok() -> bool:
    if shutil.which("node") is None:
        return False
    try:
        return validate_argtype("t: seq(a: path)").ok
    except Exception:  # BridgeUnavailable, node crash, etc. — treat as unavailable
        return False


bridge = pytest.mark.skipif(not _bridge_ok(), reason="argtype Node bridge unavailable")


@bridge
def test_validate_argtype_accepts_valid_and_flags_invalid():
    assert validate_argtype('bet: seq(a: path, opt("-f", f: float))').ok
    bad = validate_argtype("bet: seq(int, xyz(bad))")
    assert not bad.ok and bad.errors


@bridge
def test_validate_argtype_requires_quoted_digit_root():
    # AFNI-style digit-led tool names must be quoted.
    assert validate_argtype('"3dcalc": seq(a: path)').ok
    assert not validate_argtype("3dcalc: seq(a: path)").ok
