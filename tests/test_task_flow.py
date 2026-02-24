"""Tests for happy-path task workflow commands."""

from __future__ import annotations

import subprocess
from pathlib import Path

from typer.testing import CliRunner

from agentmesh import db
from agentmesh.cli import app
from agentmesh.episodes import get_current_episode

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


def test_task_start_creates_episode_and_claims(
    tmp_path: Path,
    tmp_data_dir: Path,
    monkeypatch,
) -> None:
    """task start should create an episode and apply requested claims."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENTMESH_DATA_DIR", str(tmp_data_dir))
    monkeypatch.setenv("AGENTMESH_AGENT_ID", "task_agent")

    result = runner.invoke(
        app,
        [
            "task",
            "start",
            "--title",
            "Fix login timeout",
            "--claim",
            "src/auth.py",
            "--claim",
            "tests/test_auth.py",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Episode" in result.output
    assert "Claimed" in result.output

    ep_id = get_current_episode(tmp_data_dir)
    assert ep_id.startswith("ep_")

    claims = db.list_claims(tmp_data_dir, agent_id="task_agent")
    assert len(claims) == 2
    paths = {c.path for c in claims}
    assert str((repo / "src/auth.py").resolve()) in paths
    assert str((repo / "tests/test_auth.py").resolve()) in paths


def test_task_start_reuses_current_episode(
    tmp_path: Path,
    tmp_data_dir: Path,
    monkeypatch,
) -> None:
    """task start should reuse the current episode by default."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENTMESH_DATA_DIR", str(tmp_data_dir))
    monkeypatch.setenv("AGENTMESH_AGENT_ID", "task_agent")

    first = runner.invoke(app, ["task", "start", "--title", "First task"])
    assert first.exit_code == 0, first.output
    ep1 = get_current_episode(tmp_data_dir)
    assert ep1

    second = runner.invoke(app, ["task", "start", "--title", "Second task"])
    assert second.exit_code == 0, second.output
    assert "Using episode" in second.output
    ep2 = get_current_episode(tmp_data_dir)
    assert ep2 == ep1


def test_task_finish_commits_and_closes_task(
    tmp_path: Path,
    tmp_data_dir: Path,
    monkeypatch,
) -> None:
    """task finish should commit, release claims, and end the current episode."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENTMESH_DATA_DIR", str(tmp_data_dir))
    monkeypatch.setenv("AGENTMESH_AGENT_ID", "task_agent")

    start = runner.invoke(
        app,
        ["task", "start", "--title", "Fix auth", "--claim", "src/auth.py"],
    )
    assert start.exit_code == 0, start.output
    ep_id = get_current_episode(tmp_data_dir)
    assert ep_id

    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src/auth.py").write_text("TOKEN_TIMEOUT = 30\n")
    subprocess.run(["git", "add", "src/auth.py"], cwd=str(repo), capture_output=True, check=True)

    finish = runner.invoke(
        app,
        [
            "task",
            "finish",
            "--message",
            "fix auth timeout",
            "--run-tests",
            "python -c 'print(123)'",
            "--no-capsule",
        ],
    )
    assert finish.exit_code == 0, finish.output
    assert "Committed" in finish.output
    assert "Released" in finish.output
    assert "ended" in finish.output

    # Default behavior: claims released and episode closed.
    remaining = db.list_claims(tmp_data_dir, agent_id="task_agent")
    assert remaining == []
    assert get_current_episode(tmp_data_dir) == ""

    # Commit trailer should carry the episode ID.
    log = subprocess.run(
        ["git", "log", "-1", "--format=%B"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "AgentMesh-Episode:" in log
    assert ep_id in log
