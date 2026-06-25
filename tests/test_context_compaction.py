"""Old tool results are stubbed past a char budget; everything else is kept."""

from __future__ import annotations

from styx_agent.agent import _ELIDED_MARKER, _compact_tool_results


def _msgs() -> list[dict]:
    return [
        {"role": "system", "content": "S" * 100},
        {"role": "user", "content": "U" * 100},
        {"role": "assistant", "content": "a1"},
        {"role": "tool", "tool_call_id": "1", "content": "X" * 1000},
        {"role": "assistant", "content": "a2"},
        {"role": "tool", "tool_call_id": "2", "content": "Y" * 1000},
    ]


def test_under_budget_keeps_all():
    m = _msgs()
    _compact_tool_results(m, budget=10_000)
    assert m[3]["content"] == "X" * 1000
    assert m[5]["content"] == "Y" * 1000


def test_over_budget_elides_oldest_tool_first():
    m = _msgs()
    _compact_tool_results(m, budget=1500)  # newest (Y) fits; older (X) does not
    assert m[5]["content"] == "Y" * 1000
    assert m[3]["content"].startswith(_ELIDED_MARKER)
    # non-tool messages are never touched
    assert m[0]["content"] == "S" * 100
    assert m[2]["content"] == "a1"


def test_budget_zero_disables():
    m = _msgs()
    _compact_tool_results(m, budget=0)
    assert m[3]["content"] == "X" * 1000


def test_idempotent():
    m = _msgs()
    _compact_tool_results(m, budget=1500)
    once = [x["content"] for x in m]
    _compact_tool_results(m, budget=1500)
    assert once == [x["content"] for x in m]
