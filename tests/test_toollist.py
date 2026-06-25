"""read_tool_list resolves text lists, JSON, and NiWrap descriptor directories."""

from __future__ import annotations

import json

import pytest

from styx_agent.toollist import read_tool_list


def test_text_file_trims_skips_comments_and_dedupes(tmp_path):
    p = tmp_path / "tools.txt"
    p.write_text("bet\n# a comment\n\n  fast  \nflirt\nbet\n", encoding="utf-8")
    assert read_tool_list(p) == ["bet", "fast", "flirt"]


def test_json_array_of_names(tmp_path):
    p = tmp_path / "tools.json"
    p.write_text(json.dumps(["bet", "fast", "bet"]), encoding="utf-8")
    assert read_tool_list(p) == ["bet", "fast"]


def test_json_array_of_descriptor_objects(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps([{"name": "bet"}, {"name": "fast"}, {"x": 1}]), encoding="utf-8")
    assert read_tool_list(p) == ["bet", "fast"]


def test_json_single_descriptor(tmp_path):
    p = tmp_path / "bet.json"
    p.write_text(json.dumps({"name": "bet", "command-line": "bet ..."}), encoding="utf-8")
    assert read_tool_list(p) == ["bet"]


def test_niwrap_descriptor_directory(tmp_path):
    d = tmp_path / "descriptors"
    d.mkdir()
    (d / "bet.json").write_text(json.dumps({"name": "bet"}), encoding="utf-8")
    (d / "fast.json").write_text(json.dumps({"name": "fast"}), encoding="utf-8")
    (d / "broken.json").write_text("{not json", encoding="utf-8")  # → filename stem
    assert read_tool_list(d) == ["bet", "broken", "fast"]  # sorted by filename


def test_unrecognized_json_shape_raises(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"schemaVersion": 1}), encoding="utf-8")
    with pytest.raises(ValueError):
        read_tool_list(p)
