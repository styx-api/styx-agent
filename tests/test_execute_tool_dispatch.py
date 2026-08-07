"""Malformed tool calls must come back as feedback, not as an exception.

A model that drops a required argument is emitting something it can fix on the
next turn, exactly like a bad path or an empty grep. Raising instead ends the
run and discards every token spent on it — an outputs trace is ten-plus turns of
reading, so one dropped ``path`` used to cost all of it.
"""

from __future__ import annotations

import pytest

from styx_agent.tools.filesystem import _REQUIRED, TOOL_DEFINITIONS, execute_tool


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.cpp").write_text("int main() { return 0; }", encoding="utf-8")
    return str(tmp_path)


def test_required_map_covers_every_declared_tool():
    """The check is derived from the schemas, so it cannot drift from them."""
    assert set(_REQUIRED) == {t["function"]["name"] for t in TOOL_DEFINITIONS}
    assert _REQUIRED["read_file"] == ["path"]
    assert _REQUIRED["grep"] == ["pattern"]


# The exact call that killed a bet exploration: read_file with no path.
def test_missing_required_argument_is_reported_not_raised(repo):
    result = execute_tool("read_file", {}, repo)
    assert "read_file" in result and "path" in result
    assert "Error" in result


def test_every_tool_reports_its_own_missing_argument(repo):
    for name, required in _REQUIRED.items():
        result = execute_tool(name, {}, repo)
        assert result.startswith("Error:"), f"{name} did not report a malformed call"
        for key in required:
            assert key in result, f"{name} did not name the missing '{key}'"


def test_explicit_null_counts_as_missing(repo):
    """A model that sends `path: null` means unset, and `.get(k, default)` would
    have handed the null straight through to the tool."""
    assert execute_tool("read_file", {"path": None}, repo).startswith("Error:")


def test_optional_argument_may_be_omitted_or_null(repo):
    """`path` is optional on grep/find_files, so neither form is an error."""
    for args in ({"pattern": "main"}, {"pattern": "main", "path": None}):
        assert not execute_tool("grep", args, repo).startswith("Error:")
    for args in ({"pattern": "*.cpp"}, {"pattern": "*.cpp", "path": None}):
        assert not execute_tool("find_files", args, repo).startswith("Error:")


def test_well_formed_calls_still_work(repo):
    assert "hello" in execute_tool("read_file", {"path": "a.txt"}, repo)
    assert "a.txt" in execute_tool("list_directory", {"path": "."}, repo)
    assert "b.cpp" in execute_tool("find_files", {"pattern": "*.cpp"}, repo)


def test_unknown_tool_is_still_reported(repo):
    assert "Unknown tool" in execute_tool("rm_rf", {"path": "."}, repo)
