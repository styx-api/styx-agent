"""Run histories and the resource stats beside them.

The point of a transcript is to show what the model was actually shown at the
moment a finding was made. That is not the same as the final state of the
message list: `_compact_tool_results` rewrites old tool results into stubs *in
place*, so a transcript that shared those dicts would record a stub exactly
where the evidence used to be.
"""

from __future__ import annotations

import json

from styx_agent.agent import _compact_tool_results
from styx_agent.telemetry import AgentStat, collect_agent_stats, record_agent, write_transcripts


def _stat(label: str = "interface", **over) -> AgentStat:
    base = dict(
        label=label, turns=3, seconds=1.5, prompt_tokens=100, completion_tokens=20,
        model="openai/glm-5.2",
        transcript=[
            {"role": "system", "content": "you are an agent"},
            {"role": "user", "content": "trace bet"},
            {"role": "tool", "tool_call_id": "c1", "content": "int main() {}"},
        ],
    )
    base.update(over)
    return AgentStat(**base)


# --- the property compaction would otherwise destroy ----------------------

def test_a_copied_transcript_survives_compaction():
    """Shallow copies are enough: compaction replaces `content`, it does not
    mutate anything nested. If it ever starts to, this fails."""
    tool_result = "x" * 5000
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "tool", "tool_call_id": "c1", "content": tool_result},
        {"role": "tool", "tool_call_id": "c2", "content": "recent"},
    ]
    transcript = [dict(m) for m in messages]

    _compact_tool_results(messages, budget=100)

    # The live list was stubbed, which is the whole point of compaction ...
    assert tool_result not in (messages[1].get("content") or "")
    # ... and the history still has what the model was shown at the time.
    assert transcript[1]["content"] == tool_result


# --- write_transcripts ----------------------------------------------------

def test_writes_one_jsonl_per_agent_with_a_header(tmp_path):
    written = write_transcripts([_stat("interface"), _stat("outputs")], tmp_path)
    assert {p.name for p in written} == {"interface.jsonl", "outputs.jsonl"}

    lines = (tmp_path / "transcripts" / "interface.jsonl").read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    assert header["_meta"]["label"] == "interface"
    assert header["_meta"]["model"] == "openai/glm-5.2"
    assert header["_meta"]["messages"] == 3
    # One row per message, in order.
    assert [json.loads(x)["role"] for x in lines[1:]] == ["system", "user", "tool"]
    assert json.loads(lines[3])["content"] == "int main() {}"


def test_a_repeated_label_does_not_overwrite(tmp_path):
    """Losing the first of two histories is the exact failure this prevents."""
    written = write_transcripts([_stat("author"), _stat("author")], tmp_path)
    assert [p.name for p in written] == ["author.jsonl", "author.2.jsonl"]


def test_labels_that_are_not_filenames_are_made_safe(tmp_path):
    written = write_transcripts([_stat("scan:parsing")], tmp_path)
    assert written[0].name == "scan_parsing.jsonl"


def test_no_transcripts_writes_nothing(tmp_path):
    assert write_transcripts([_stat(transcript=[])], tmp_path) == []
    assert not (tmp_path / "transcripts").exists()


# --- the aggregate view stays small ---------------------------------------

def test_to_dict_summarises_the_transcript_rather_than_embedding_it():
    """`to_dict()` goes into run.json, results.jsonl and meta.json. Folding a
    megabyte of history into each would make all three unreadable."""
    stat = _stat()
    summary = stat.to_dict()
    assert summary["messages"] == 3
    assert "transcript" not in summary
    assert "int main() {}" not in json.dumps(summary)
    # The things a run has to be reproducible from are all present.
    assert summary["model"] == "openai/glm-5.2"
    assert summary["total_tokens"] == 120


def test_recording_a_stat_never_needs_provider_credentials(monkeypatch):
    """Telemetry must not be able to crash the run it is measuring.

    Recording used to resolve the model string for its `model` field, and
    `resolve_model` raises when the provider's key is absent - so on a machine
    without credentials (CI), finishing an author run raised from its own
    `finally` block and masked whatever the run was actually doing.
    """
    import asyncio

    from styx_agent.author import argtype as argtype_mod

    monkeypatch.delenv("NEURODESK_KEY", raising=False)

    async def fake_complete(messages, model):
        return "t: seq(a: path)", 10, 5

    monkeypatch.setattr(argtype_mod, "ensure_bridge", lambda: None)
    monkeypatch.setattr(argtype_mod, "_complete", fake_complete)
    monkeypatch.setattr(argtype_mod, "validate_argtype", lambda src: type("V", (), {"ok": True, "errors": [], "warnings": [], "n_nodes": 3})())

    with collect_agent_stats() as stats:
        asyncio.run(argtype_mod.author_argtype("t", "iface", "outs", model="neurodesk/glm-5.2"))

    assert len(stats) == 1
    # The requested string is what reproduces the run, so that is what is kept.
    assert stats[0].model == "neurodesk/glm-5.2"


def test_stats_are_still_a_no_op_outside_a_collection_scope():
    record_agent(_stat())  # must not raise
    with collect_agent_stats() as stats:
        record_agent(_stat())
    assert len(stats) == 1
    record_agent(_stat())
    assert len(stats) == 1
