"""Tests for agentmesh commit (git-weave bridge) -- real git repos, no mocking."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from agentmesh import db, events
from agentmesh.cli import EPISODE_TRAILER_KEY, app
from agentmesh.episodes import start_episode, get_current_episode
from agentmesh.gitbridge import get_staged_diff, get_staged_files, compute_patch_hash, git_commit
from agentmesh.models import EventKind
from agentmesh.weaver import append_weave, export_weave_md, verify_weave

runner = CliRunner()


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

    trailer = f"{EPISODE_TRAILER_KEY}: {ep_id}"
    ok, sha, err = git_commit("add bar", trailer=trailer, cwd=str(repo))
    assert ok

    log = subprocess.run(
        ["git", "log", "-1", "--format=%B"], cwd=str(repo),
        capture_output=True, text=True,
    ).stdout
    assert ep_id in log
    assert f"{EPISODE_TRAILER_KEY}:" in log


def test_cli_commit_trailer_matches_action_parser(
    tmp_path: Path,
    tmp_data_dir: Path,
    monkeypatch,
) -> None:
    """CLI trailer must be parseable via git's trailer key query used by agentmesh-action."""
    repo = _init_repo(tmp_path / "repo")
    ep_id = start_episode(title="trailer parser", data_dir=tmp_data_dir)
    (repo / "parsed.py").write_text("v = 1\n")
    subprocess.run(["git", "add", "parsed.py"], cwd=str(repo), capture_output=True, check=True)

    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENTMESH_DATA_DIR", str(tmp_data_dir))
    monkeypatch.setenv("AGENTMESH_AGENT_ID", "trailer_agent")
    result = runner.invoke(app, ["commit", "-m", "add parsed"])
    assert result.exit_code == 0, result.output

    parsed = subprocess.run(
        [
            "git",
            "log",
            "-1",
            f"--format=%(trailers:key={EPISODE_TRAILER_KEY},valueonly)",
        ],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert parsed == ep_id


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


# -- CLI integration tests --


def test_cli_commit_creates_weave_and_event(tmp_path: Path, tmp_data_dir: Path, monkeypatch) -> None:
    """End-to-end: agentmesh commit via CLI creates weave event + COMMIT event."""
    repo = _init_repo(tmp_path / "repo")
    (repo / "hello.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "hello.py"], cwd=str(repo), capture_output=True, check=True)

    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENTMESH_DATA_DIR", str(tmp_data_dir))
    monkeypatch.setenv("AGENTMESH_AGENT_ID", "cli_test_agent")

    result = runner.invoke(app, ["commit", "-m", "add hello", "--no-episode-trailer"])
    assert result.exit_code == 0, result.output
    assert "Committed" in result.output
    assert "hello.py" in result.output

    # Verify weave event was created
    evts = db.list_weave_events(tmp_data_dir)
    assert len(evts) == 1
    assert evts[0].git_commit_sha
    assert "hello.py" in evts[0].affected_symbols


def test_cli_commit_not_git_repo(tmp_path: Path, tmp_data_dir: Path, monkeypatch) -> None:
    """CLI exits 1 when not in a git repo."""
    non_repo = tmp_path / "not-git"
    non_repo.mkdir()
    monkeypatch.chdir(non_repo)
    monkeypatch.setenv("AGENTMESH_DATA_DIR", str(tmp_data_dir))

    result = runner.invoke(app, ["commit", "-m", "nope"])
    assert result.exit_code == 1
    assert "Not a git repository" in result.output


def test_cli_commit_nothing_staged(tmp_path: Path, tmp_data_dir: Path, monkeypatch) -> None:
    """CLI exits 1 when nothing is staged."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENTMESH_DATA_DIR", str(tmp_data_dir))

    result = runner.invoke(app, ["commit", "-m", "empty"])
    assert result.exit_code == 1
    assert "Nothing staged" in result.output


def test_cli_commit_capsule_links_to_weave(tmp_path: Path, tmp_data_dir: Path, monkeypatch) -> None:
    """--capsule should produce a weave event with capsule_id set."""
    repo = _init_repo(tmp_path / "repo")
    (repo / "linked.py").write_text("z = 99\n")
    subprocess.run(["git", "add", "linked.py"], cwd=str(repo), capture_output=True, check=True)

    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENTMESH_DATA_DIR", str(tmp_data_dir))
    monkeypatch.setenv("AGENTMESH_AGENT_ID", "cap_agent")

    result = runner.invoke(app, ["commit", "-m", "with capsule", "--capsule", "--no-episode-trailer"])
    assert result.exit_code == 0, result.output

    evts = db.list_weave_events(tmp_data_dir)
    assert len(evts) == 1
    assert evts[0].capsule_id.startswith("cap_")


def test_weave_verify_beyond_100_events(tmp_data_dir: Path) -> None:
    """Weave verify must check ALL events, not just first 100."""
    # Create 105 valid events
    for i in range(105):
        append_weave(
            git_commit_sha=f"sha_{i:04d}",
            affected_symbols=[f"file_{i}.py"],
            data_dir=tmp_data_dir,
        )

    # All 105 should be returned
    all_evts = db.list_weave_events(tmp_data_dir)
    assert len(all_evts) == 105

    # Verify passes on full chain
    valid, err = verify_weave(tmp_data_dir)
    assert valid, err
