"""CLI tests for the worker sub-app."""

from __future__ import annotations

import json
from unittest.mock import patch, MagicMock
from dataclasses import dataclass

from typer.testing import CliRunner

from agentmesh import db
from agentmesh.cli import app
from agentmesh.models import Agent
from agentmesh import orchestrator, spawner

runner = CliRunner()


def _invoke(args: list[str], tmp_path):
    return runner.invoke(app, ["--data-dir", str(tmp_path)] + args)


def _setup(tmp_path):
    db.init_db(tmp_path)
    a = Agent(agent_id="agent_cli", cwd="/tmp")
    db.register_agent(a, tmp_path)


def _make_assigned_task(tmp_path, branch="feat/cli-test"):
    task = orchestrator.create_task("CLI spawn test", data_dir=tmp_path)
    orchestrator.assign_task(task.task_id, "agent_cli", branch=branch, data_dir=tmp_path)
    return task.task_id


# -- worker spawn --

def test_worker_spawn_json(tmp_path):
    _setup(tmp_path)
    task_id = _make_assigned_task(tmp_path)

    fake_record = spawner.SpawnRecord(
        spawn_id="spawn_abc123",
        task_id=task_id,
        attempt_id="att_xyz",
        agent_id="agent_cli",
        pid=12345,
        worktree_path="/tmp/wt",
        branch="feat/cli-test",
        episode_id="",
        context_hash="sha256:abc",
        started_at="2026-01-01T00:00:00Z",
        output_path="/tmp/wt/.agentmesh/claude_output.json",
        backend="claude_code",
        backend_version="agentmesh:0.7.0",
    )

    with patch("agentmesh.spawner.spawn", return_value=fake_record):
        result = _invoke(["worker", "spawn", task_id, "--agent", "agent_cli", "--json"], tmp_path)

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["spawn_id"] == "spawn_abc123"
    assert data["pid"] == 12345
    assert data["backend"] == "claude_code"
    assert data["backend_version"] == "agentmesh:0.7.0"


# -- worker check --

def test_worker_check_json(tmp_path):
    _setup(tmp_path)
    fake_result = spawner.CheckResult(spawn_id="spawn_abc123", running=True, exit_code=None)

    with patch("agentmesh.spawner.check", return_value=fake_result):
        result = _invoke(["worker", "check", "spawn_abc123", "--json"], tmp_path)

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["running"] is True


# -- worker list --

def test_worker_list_empty(tmp_path):
    _setup(tmp_path)
    with patch("agentmesh.spawner.list_spawns", return_value=[]):
        result = _invoke(["worker", "list"], tmp_path)
    assert result.exit_code == 0
    assert "No workers" in result.output


def test_worker_list_json(tmp_path):
    _setup(tmp_path)
    fake_record = spawner.SpawnRecord(
        spawn_id="spawn_abc123",
        task_id="task_xyz",
        attempt_id="att_xyz",
        agent_id="agent_cli",
        pid=12345,
        worktree_path="/tmp/wt",
        branch="feat/test",
        episode_id="",
        context_hash="sha256:abc",
        started_at="2026-01-01T00:00:00Z",
    )

    with patch("agentmesh.spawner.list_spawns", return_value=[fake_record]):
        result = _invoke(["worker", "list", "--json"], tmp_path)

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 1
    assert data[0]["spawn_id"] == "spawn_abc123"


def test_worker_backends_json(tmp_path):
    _setup(tmp_path)
    from agentmesh.worker_adapters import AdapterInfo

    infos = [AdapterInfo(name="claude_code", version="agentmesh:0.7.0")]
    with patch("agentmesh.worker_adapters.list_adapters", return_value=infos):
        with patch("agentmesh.worker_adapters.get_adapter_load_errors", return_value=[]):
            result = _invoke(["worker", "backends", "--json"], tmp_path)

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["backends"][0]["name"] == "claude_code"
    assert data["backends"][0]["version"] == "agentmesh:0.7.0"
