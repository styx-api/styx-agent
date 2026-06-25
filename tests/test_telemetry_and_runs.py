"""Telemetry collection, run-dir paths, and the per-tool descriptor summary."""

from __future__ import annotations

import json

from styx_agent import _descriptor_summary
from styx_agent.paths import run_dir, runs_dir
from styx_agent.telemetry import AgentStat, collect_agent_stats, record_agent


def test_record_outside_scope_is_noop():
    record_agent(AgentStat("x", 1, 0.0))  # must not raise with no active sink


def test_collect_agent_stats_gathers_records():
    with collect_agent_stats() as stats:
        record_agent(AgentStat("a", 3, 1.5, prompt_tokens=10, completion_tokens=5))
        record_agent(AgentStat("b", 1, 0.5))
    assert [s.label for s in stats] == ["a", "b"]
    assert stats[0].total_tokens == 15
    assert stats[0].to_dict()["total_tokens"] == 15


def test_collect_scopes_are_isolated():
    with collect_agent_stats() as outer:
        record_agent(AgentStat("outer", 1, 0.0))
        with collect_agent_stats() as inner:
            record_agent(AgentStat("inner", 1, 0.0))
        assert [s.label for s in inner] == ["inner"]
        record_agent(AgentStat("outer2", 1, 0.0))
    assert [s.label for s in outer] == ["outer", "outer2"]


def test_run_dir_layout(tmp_path):
    rd = run_dir("2026-06-25T00-00-00Z", tmp_path)
    assert rd == runs_dir(tmp_path) / "2026-06-25T00-00-00Z"
    assert rd.parent.name == "runs"


def test_descriptor_summary(tmp_path):
    dest = tmp_path / "tool"
    dest.mkdir()
    (dest / "boutiques.json").write_text(
        json.dumps({"inputs": [{"id": "a"}, {"id": "b"}], "output-files": [{"id": "o"}]}),
        encoding="utf-8",
    )
    (dest / "interface.md").write_text(
        "x <!-- source: a.c:1-2 --> y <!-- source: b.c:3 -->", encoding="utf-8"
    )
    (dest / "outputs.md").write_text("<!-- source: c.c:9 -->", encoding="utf-8")
    assert _descriptor_summary(dest) == {"n_inputs": 2, "n_outputs": 1, "n_source_refs": 3}


def test_descriptor_summary_missing_artifacts(tmp_path):
    dest = tmp_path / "empty"
    dest.mkdir()
    assert _descriptor_summary(dest) == {"n_source_refs": 0}
