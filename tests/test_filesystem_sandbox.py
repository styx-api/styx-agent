"""Verify filesystem tools cannot escape the configured repo root.

Threat model: third-party source repos we scan may contain prompt-injection
payloads in comments or docstrings. If the LLM is coerced into issuing a
malicious tool call, the filesystem tools must refuse to read anything
outside ``repo_root``.
"""

from __future__ import annotations

import os
import sys

import pytest

from styx_agent.tools.filesystem import _resolve, list_directory, read_file


@pytest.fixture
def sandbox(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "inside.txt").write_text("inside", encoding="utf-8")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("SECRET", encoding="utf-8")

    sibling = tmp_path / "repo-evil"
    sibling.mkdir()
    (sibling / "prefix-trick.txt").write_text("PWNED", encoding="utf-8")

    return repo, outside, sibling


def test_dotdot_traversal_clamps_to_root(sandbox):
    repo, outside, _ = sandbox
    resolved = _resolve("../outside/secret.txt", str(repo))
    assert resolved == repo.resolve()


def test_absolute_path_clamps_to_root(sandbox):
    repo, outside, _ = sandbox
    resolved = _resolve(str(outside / "secret.txt"), str(repo))
    assert resolved == repo.resolve()


def test_sibling_prefix_collision_is_rejected(sandbox):
    """/tmp/repo-evil must not pass a startswith('/tmp/repo') check."""
    repo, _, sibling = sandbox
    resolved = _resolve(f"../{sibling.name}/prefix-trick.txt", str(repo))
    assert resolved == repo.resolve()
    # read_file on the clamped path returns "not a file" (root is a dir).
    result = read_file(f"../{sibling.name}/prefix-trick.txt", str(repo))
    assert "PWNED" not in result


@pytest.mark.skipif(
    sys.platform == "win32" and not os.environ.get("CI"),
    reason="symlink creation on Windows needs developer mode or admin",
)
def test_symlink_escape_is_rejected(sandbox):
    repo, outside, _ = sandbox
    link = repo / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"cannot create symlink in this environment: {e}")

    # Listing through the symlink must not expose outside/secret.txt.
    listing = list_directory("escape", str(repo))
    assert "secret.txt" not in listing

    content = read_file("escape/secret.txt", str(repo))
    assert "SECRET" not in content


def test_in_repo_access_still_works(sandbox):
    repo, _, _ = sandbox
    result = read_file("inside.txt", str(repo))
    assert "inside" in result
