"""``media-types`` is optional metadata allowed only on File inputs / outputs."""

from __future__ import annotations

from styx_agent.author.validator import SCHEMA_VERSION, validate


def _base() -> dict:
    """A minimal descriptor that validates clean, to mutate per-test."""
    return {
        "name": "demo",
        "schema-version": SCHEMA_VERSION,
        "description": "demo tool",
        "command-line": "demo [IN]",
        "inputs": [
            {"id": "in_file", "name": "In", "type": "File", "value-key": "[IN]"},
        ],
        "output-files": [
            {"id": "out", "name": "Out", "path-template": "[IN].nii.gz"},
        ],
    }


def _messages(data: dict) -> str:
    return "\n".join(e.format() for e in validate(data))


def test_base_is_valid():
    assert validate(_base()) == []


def test_media_types_accepted_on_file_input_and_output():
    data = _base()
    data["inputs"][0]["media-types"] = ["application/dicom"]
    data["output-files"][0]["media-types"] = ["application/x-nifti"]
    assert validate(data) == []


def test_media_types_rejected_on_non_file_input():
    data = _base()
    data["inputs"][0] = {
        "id": "level", "name": "Level", "type": "Number", "integer": True,
        "value-key": "[IN]", "media-types": ["text/plain"],
    }
    msgs = _messages(data)
    assert "media-types" in msgs
    assert "File inputs" in msgs  # the hint steers the model back


def test_media_types_must_be_nonempty_list_of_strings():
    for bad in (["application/dicom", 3], "application/dicom", []):
        data = _base()
        data["output-files"][0]["media-types"] = bad
        assert any("media-types" in e.format() for e in validate(data)), bad
