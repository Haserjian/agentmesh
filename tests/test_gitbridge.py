"""Tests for gitbridge -- real git repos in tmp_path, no mocking."""

from __future__ import annotations

import subprocess
from pathlib import Path

from agentmesh.gitbridge import (
    compute_patch_hash,
    get_staged_diff,
    get_staged_files,
    git_commit,
    is_git_repo,
)


def _init_repo(tmp_path: Path) -> Path:
    """Create a git repo in tmp_path and return its path."""
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(tmp_path), capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmp_path), capture_output=True, check=True,
    )
    return tmp_path


def test_is_git_repo(tmp_path: Path) -> None:
    non_repo = tmp_path / "not-git"
    non_repo.mkdir()
    assert not is_git_repo(str(non_repo))

    repo = _init_repo(tmp_path / "repo")
    assert is_git_repo(str(repo))


def test_staged_files_and_diff(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    # Need an initial commit for diff --cached to work
    (repo / "init.txt").write_text("init\n")
    subprocess.run(["git", "add", "init.txt"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True, check=True)

    # Stage a new file
    (repo / "foo.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "foo.py"], cwd=str(repo), capture_output=True, check=True)

    files = get_staged_files(str(repo))
    assert "foo.py" in files

    diff = get_staged_diff(str(repo))
    assert "x = 1" in diff


def test_compute_patch_hash() -> None:
    diff = "diff --git a/foo.py b/foo.py\n+x = 1\n"
    h1 = compute_patch_hash(diff)
    h2 = compute_patch_hash(diff)
    assert h1 == h2
    assert h1.startswith("sha256:")
    assert len(h1) == 7 + 64  # "sha256:" + 64 hex chars

    # Different input -> different hash
    h3 = compute_patch_hash(diff + "extra")
    assert h3 != h1


def test_git_commit_with_trailer(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "bar.py").write_text("y = 2\n")
    subprocess.run(["git", "add", "bar.py"], cwd=str(repo), capture_output=True, check=True)

    ok, sha, err = git_commit(
        "add bar", trailer="Episode: ep_test123", cwd=str(repo),
    )
    assert ok, f"commit failed: {err}"
    assert len(sha) == 40  # full SHA
    assert err == ""

    # Verify trailer in log
    log = subprocess.run(
        ["git", "log", "-1", "--format=%B"], cwd=str(repo),
        capture_output=True, text=True,
    ).stdout
    assert "add bar" in log
    assert "Episode: ep_test123" in log
