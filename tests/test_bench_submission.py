"""The bench adapter's submission shape.

styx-bench validates what it is handed, so the parts worth pinning here are the
ones a type error would reject outright or a mapping error would score wrong:
one authoring call per tool shared across that tool's challenges, no null-valued
optional keys, and a failed author recorded as an abstention rather than raised.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "bench_submission",
    Path(__file__).resolve().parents[1] / "scripts" / "bench_submission.py",
)
assert _SPEC and _SPEC.loader
bench_submission = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bench_submission)


MANIFEST = {
    "challenges": [
        {"id": "bet-a", "class": "types", "generic": False, "host": {"repo": "fsl", "tool": "bet"}},
        {"id": "bet-b", "class": "outputs", "generic": False, "host": {"repo": "fsl", "tool": "bet"}},
        {"id": "generic/well-formed/fsl/bet", "class": "well-formedness", "generic": True,
         "host": {"repo": "fsl", "tool": "bet"}},
        {"id": "tar-a", "class": "surface", "generic": False, "host": {"repo": "gnu", "tool": "tar"}},
    ],
    "corpus": [
        {"id": "fsl/bet", "package": "fsl", "tool": "bet"},
        {"id": "gnu/tar", "package": "gnu", "tool": "tar"},
    ],
}


@pytest.fixture
def workspace(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(MANIFEST), encoding="utf-8")

    reports = tmp_path / "frozen"
    for pkg, tool in (("fsl", "bet"), ("gnu", "tar")):
        d = reports / pkg / tool
        d.mkdir(parents=True)
        (d / "interface.md").write_text(f"# {tool} interface", encoding="utf-8")
        (d / "outputs.md").write_text(f"# {tool} outputs", encoding="utf-8")
    return manifest, reports, tmp_path / "submission.json"


def _args(manifest, reports, out, **over):
    import argparse

    base = dict(
        manifest=str(manifest), reports=str(reports), out=str(out),
        model="test/model", system=None, version=None, run=None, max_retries=1,
    )
    base.update(over)
    return argparse.Namespace(**base)


def _run(args) -> dict:
    """Drive one model, the way the sweep does for each of its models."""
    import asyncio

    out = Path(args.out)
    assert asyncio.run(bench_submission._run(args, args.model, out)) == 0
    return json.loads(out.read_text(encoding="utf-8"))


def test_authors_once_per_tool_and_shares_the_document(workspace, monkeypatch):
    calls: list[str] = []

    async def fake_author(tool, iface, outs, model, max_retries):
        calls.append(tool)
        return f"{tool}: seq(a: path)"

    monkeypatch.setattr(bench_submission, "author_argtype", fake_author)
    sub = _run(_args(*workspace))

    # Two tools, four challenges: the document is authored per tool, not per challenge.
    assert sorted(calls) == ["bet", "tar"]
    assert set(sub["answers"]) == {"bet-a", "bet-b", "generic/well-formed/fsl/bet", "tar-a"}
    assert sub["answers"]["bet-a"]["source"] == sub["answers"]["bet-b"]["source"] == "bet: seq(a: path)"
    assert sub["answers"]["tar-a"]["source"] == "tar: seq(a: path)"
    # Generic challenges are answered too - they are scored like any other.
    assert sub["answers"]["generic/well-formed/fsl/bet"]["source"] == "bet: seq(a: path)"


# The bench validates types, so a null-valued optional key is rejected outright
# where an absent one is fine. This was a real round-trip failure.
def test_omits_optional_keys_rather_than_sending_null(workspace, monkeypatch):
    async def fake_author(tool, iface, outs, model, max_retries):
        return "x: seq(a: path)"

    monkeypatch.setattr(bench_submission, "author_argtype", fake_author)
    sub = _run(_args(*workspace))
    assert "version" not in sub and "run" not in sub
    assert not [k for k, v in sub.items() if v is None]

    sub = _run(_args(*workspace, version="1.2.3", run=2))
    assert sub["version"] == "1.2.3" and sub["run"] == 2


def test_a_failed_author_is_an_abstention_not_a_crash(workspace, monkeypatch):
    async def boom(tool, iface, outs, model, max_retries):
        if tool == "bet":
            raise ValueError("descriptor still invalid after 1 retries")
        return "tar: seq(a: path)"

    monkeypatch.setattr(bench_submission, "author_argtype", boom)
    sub = _run(_args(*workspace))
    assert "still invalid" in sub["answers"]["bet-a"]["error"]
    assert "source" not in sub["answers"]["bet-a"]
    # One tool failing must not take the others down with it.
    assert sub["answers"]["tar-a"]["source"] == "tar: seq(a: path)"


def test_missing_frozen_reports_abstain_with_a_reason(workspace, monkeypatch, tmp_path):
    manifest, _reports, out = workspace

    async def fake_author(tool, iface, outs, model, max_retries):
        raise AssertionError("must not author without reports")

    monkeypatch.setattr(bench_submission, "author_argtype", fake_author)
    sub = _run(_args(manifest, tmp_path / "absent", out))
    assert all("no frozen report" in a["error"] for a in sub["answers"].values())


def test_slug_names_a_file_after_the_model():
    assert bench_submission._slug("neurodesk/glm-5.2") == "glm-5.2"
    assert bench_submission._slug("litellm_proxy/bedrock/us.anthropic.claude-sonnet-4-6") == (
        "us.anthropic.claude-sonnet-4-6"
    )
    assert bench_submission._slug("weird/name with spaces") == "name_with_spaces"


def test_sweep_writes_one_submission_per_model(workspace, monkeypatch, tmp_path):
    """`--models` is the whole point of the sweep: one command, N submissions."""
    manifest, reports, _out = workspace
    seen: list[str] = []

    async def fake_author(tool, iface, outs, model, max_retries):
        seen.append(model)
        return f"{tool}: seq(a: path)"

    monkeypatch.setattr(bench_submission, "author_argtype", fake_author)
    monkeypatch.setattr(bench_submission, "ensure_bridge", lambda: None)
    out_dir = tmp_path / "subs"
    monkeypatch.setattr(
        "sys.argv",
        ["bench_submission.py", "--manifest", str(manifest), "--reports", str(reports),
         "--models", "neurodesk/glm-5.2,neurodesk/qwen3", "--out-dir", str(out_dir)],
    )
    assert bench_submission.main() == 0

    written = sorted(p.name for p in out_dir.glob("*.json"))
    assert written == ["glm-5.2.json", "qwen3.json"]
    # Each submission names its own model, or the bench cannot pair the runs.
    systems = [json.loads((out_dir / n).read_text(encoding="utf-8"))["system"] for n in written]
    assert systems == ["styx-agent/neurodesk/glm-5.2", "styx-agent/neurodesk/qwen3"]
    assert set(seen) == {"neurodesk/glm-5.2", "neurodesk/qwen3"}


def test_one_model_failing_does_not_end_the_sweep(workspace, monkeypatch, tmp_path):
    manifest, reports, _out = workspace

    async def fake_author(tool, iface, outs, model, max_retries):
        if model.endswith("qwen3"):
            raise RuntimeError("gateway said no")
        return f"{tool}: seq(a: path)"

    monkeypatch.setattr(bench_submission, "author_argtype", fake_author)
    monkeypatch.setattr(bench_submission, "ensure_bridge", lambda: None)
    out_dir = tmp_path / "subs"
    monkeypatch.setattr(
        "sys.argv",
        ["bench_submission.py", "--manifest", str(manifest), "--reports", str(reports),
         "--models", "neurodesk/qwen3,neurodesk/glm-5.2", "--out-dir", str(out_dir)],
    )
    # A per-model failure is caught inside `_author_one` and recorded as an
    # abstention, so the sweep still writes both files and exits clean.
    assert bench_submission.main() == 0
    assert sorted(p.name for p in out_dir.glob("*.json")) == ["glm-5.2.json", "qwen3.json"]
    failed = json.loads((out_dir / "qwen3.json").read_text(encoding="utf-8"))
    assert all("error" in a for a in failed["answers"].values())


def test_system_defaults_to_the_author_model(workspace, monkeypatch):
    async def fake_author(tool, iface, outs, model, max_retries):
        return "x: seq(a: path)"

    monkeypatch.setattr(bench_submission, "author_argtype", fake_author)
    assert _run(_args(*workspace))["system"] == "styx-agent/test/model"
    assert _run(_args(*workspace, system="glm-5.2-author"))["system"] == "glm-5.2-author"
