"""Tests for happy-path task workflow commands."""

from __future__ import annotations

import json
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


def test_task_commands_use_policy_defaults(
    tmp_path: Path,
    tmp_data_dir: Path,
    monkeypatch,
) -> None:
    """task start/finish should apply defaults from .agentmesh/policy.json."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENTMESH_DATA_DIR", str(tmp_data_dir))
    monkeypatch.setenv("AGENTMESH_AGENT_ID", "task_agent")

    policy_dir = repo / ".agentmesh"
    policy_dir.mkdir(parents=True, exist_ok=True)
    policy = {
        "schema_version": "1.0",
        "claims": {"ttl_seconds": 123},
        "task_finish": {
            "run_tests": "python -c \"open('policy_ran.txt','w').write('ok')\"",
            "capsule": False,
            "release_all": False,
            "end_episode": False,
        },
    }
    (policy_dir / "policy.json").write_text(json.dumps(policy))

    (repo / "src").mkdir(parents=True, exist_ok=True)
    start = runner.invoke(
        app,
        ["task", "start", "--title", "Policy task", "--claim", "src/policy.py"],
    )
    assert start.exit_code == 0, start.output

    active_claims = db.list_claims(tmp_data_dir, agent_id="task_agent")
    assert len(active_claims) == 1
    assert active_claims[0].ttl_s == 123

    (repo / "src/policy.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "add", "src/policy.py"], cwd=str(repo), capture_output=True, check=True)

    finish = runner.invoke(
        app,
        ["task", "finish", "--message", "policy defaults"],
    )
    assert finish.exit_code == 0, finish.output

    # Policy run_tests command should have run.
    assert (repo / "policy_ran.txt").exists()

    # Policy says no capsule/release/end; all should be preserved.
    assert db.list_capsules(tmp_data_dir) == []
    assert len(db.list_claims(tmp_data_dir, agent_id="task_agent")) == 1
    assert get_current_episode(tmp_data_dir) != ""
