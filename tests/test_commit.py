"""Tests for agentmesh commit (git-weave bridge) -- real git repos, no mocking."""

from __future__ import annotations

import subprocess
from pathlib import Path

from agentmesh import db, events
from agentmesh.episodes import start_episode, get_current_episode
from agentmesh.gitbridge import get_staged_diff, get_staged_files, compute_patch_hash, git_commit
from agentmesh.models import EventKind
from agentmesh.weaver import append_weave, export_weave_md


def _init_repo(tmp_path: Path) -> Path:
    """Create a git repo with initial commit."""
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(tmp_path), capture_output=True, check=True)
    (tmp_path / "init.txt").write_text("init\n")
    subprocess.run(["git", "add", "init.txt"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True, check=True)
    return tmp_path


def test_commit_creates_weave_event(tmp_path: Path, tmp_data_dir: Path) -> None:
    """Stage + commit -> weave event with SHA, patch hash, and files."""
    repo = _init_repo(tmp_path / "repo")
    (repo / "foo.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "foo.py"], cwd=str(repo), capture_output=True, check=True)

    staged = get_staged_files(str(repo))
    diff = get_staged_diff(str(repo))
    patch_hash = compute_patch_hash(diff)

    ok, sha, err = git_commit("add foo", cwd=str(repo))
    assert ok

    evt = append_weave(
        git_commit_sha=sha, git_patch_hash=patch_hash,
        affected_symbols=staged, data_dir=tmp_data_dir,
    )

    assert evt.git_commit_sha == sha
    assert evt.git_patch_hash == patch_hash
    assert "foo.py" in evt.affected_symbols


def test_commit_with_episode_trailer(tmp_path: Path, tmp_data_dir: Path) -> None:
    """When episode is active, trailer should appear in git log."""
    repo = _init_repo(tmp_path / "repo")
    ep_id = start_episode(title="bridge test", data_dir=tmp_data_dir)

    (repo / "bar.py").write_text("y = 2\n")
    subprocess.run(["git", "add", "bar.py"], cwd=str(repo), capture_output=True, check=True)

    trailer = f"AgentMesh-Episode: {ep_id}"
    ok, sha, err = git_commit("add bar", trailer=trailer, cwd=str(repo))
    assert ok

    log = subprocess.run(
        ["git", "log", "-1", "--format=%B"], cwd=str(repo),
        capture_output=True, text=True,
    ).stdout
    assert ep_id in log
    assert "AgentMesh-Episode:" in log


def test_commit_without_staged_files(tmp_path: Path) -> None:
    """get_staged_files returns empty list when nothing is staged."""
    repo = _init_repo(tmp_path / "repo")
    assert get_staged_files(str(repo)) == []


def test_commit_not_git_repo(tmp_path: Path) -> None:
    """is_git_repo returns False for non-repo dirs."""
    from agentmesh.gitbridge import is_git_repo
    non_repo = tmp_path / "not-git"
    non_repo.mkdir()
    assert not is_git_repo(str(non_repo))


def test_commit_event_logged(tmp_path: Path, tmp_data_dir: Path) -> None:
    """COMMIT event should appear in event log with correct payload."""
    repo = _init_repo(tmp_path / "repo")
    (repo / "qux.py").write_text("z = 3\n")
    subprocess.run(["git", "add", "qux.py"], cwd=str(repo), capture_output=True, check=True)

    staged = get_staged_files(str(repo))
    diff = get_staged_diff(str(repo))
    patch_hash = compute_patch_hash(diff)
    ok, sha, _ = git_commit("add qux", cwd=str(repo))
    assert ok

    evt = append_weave(
        git_commit_sha=sha, git_patch_hash=patch_hash,
        affected_symbols=staged, data_dir=tmp_data_dir,
    )

    events.append_event(
        EventKind.COMMIT, agent_id="test_agent",
        payload={"sha": sha, "patch_hash": patch_hash, "files": staged, "weave_event_id": evt.event_id},
        data_dir=tmp_data_dir,
    )

    # Read back from event log
    log_file = tmp_data_dir / "events.jsonl"
    assert log_file.exists()
    import json
    log_entries = [json.loads(line) for line in log_file.read_text().splitlines()]
    commit_entries = [e for e in log_entries if e.get("kind") == "COMMIT"]
    assert len(commit_entries) == 1
    assert commit_entries[0]["payload"]["sha"] == sha
    assert commit_entries[0]["payload"]["files"] == staged


def test_weave_export_md_with_file_table(tmp_data_dir: Path) -> None:
    """Weave events with git_commit_sha produce a file-change table in MD export."""
    # Create two weave events with overlapping files
    append_weave(
        git_commit_sha="abc1234567890", git_patch_hash="sha256:aaa",
        affected_symbols=["src/auth.py", "src/main.py"],
        data_dir=tmp_data_dir,
    )
    append_weave(
        git_commit_sha="def6789012345", git_patch_hash="sha256:bbb",
        affected_symbols=["src/auth.py", "tests/test_auth.py"],
        data_dir=tmp_data_dir,
    )

    md = export_weave_md(data_dir=tmp_data_dir)

    assert "## Files Changed" in md
    assert "| `src/auth.py` | abc12345, def67890 |" in md
    assert "| `src/main.py` | abc12345 |" in md
    assert "| `tests/test_auth.py` | def67890 |" in md
