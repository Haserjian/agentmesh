"""Tests for the task start/finish <-> orchestrator bridge."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from agentmesh import db, orchestrator, episodes
from agentmesh.cli import app
from agentmesh.models import Agent, TaskState

runner = CliRunner()


def _invoke(args: list[str], tmp_path):
    return runner.invoke(app, ["--data-dir", str(tmp_path)] + args)


def _setup(tmp_path):
    db.init_db(tmp_path)
    a = Agent(agent_id="agent_bridge", cwd="/tmp")
    db.register_agent(a, tmp_path)


def test_task_start_with_orch_task(tmp_path):
    """task start --orch-task should transition the orch task to RUNNING."""
    _setup(tmp_path)
    # Create and assign an orch task
    task = orchestrator.create_task("Bridge test", data_dir=tmp_path)
    orchestrator.assign_task(task.task_id, "agent_bridge", data_dir=tmp_path)

    result = _invoke(
        ["task", "start", "--title", "working", "--agent", "agent_bridge",
         "--orch-task", task.task_id],
        tmp_path,
    )
    assert result.exit_code == 0
    assert "running" in result.output

    # Verify DB state
    updated = db.get_task(task.task_id, tmp_path)
    assert updated.state == TaskState.RUNNING


def test_task_start_orch_task_not_found(tmp_path):
    """task start --orch-task with bad ID should fail."""
    _setup(tmp_path)
    result = _invoke(
        ["task", "start", "--title", "bad", "--agent", "agent_bridge",
         "--orch-task", "task_nonexistent"],
        tmp_path,
    )
    assert result.exit_code == 1
    assert "not found" in result.output


def test_task_start_orch_task_sets_current_episode(tmp_path):
    """Orch bridge should set current episode to task's episode context."""
    _setup(tmp_path)
    ep_id = episodes.start_episode(title="orch context", data_dir=tmp_path)
    task = orchestrator.create_task("Bridge episode", episode_id=ep_id, data_dir=tmp_path)
    orchestrator.assign_task(task.task_id, "agent_bridge", data_dir=tmp_path)

    # Clear current episode to prove task start writes it.
    episodes.end_episode(data_dir=tmp_path)
    assert episodes.get_current_episode(tmp_path) == ""

    result = _invoke(
        ["task", "start", "--title", "work", "--agent", "agent_bridge", "--orch-task", task.task_id],
        tmp_path,
    )
    assert result.exit_code == 0, result.output
    assert "Using episode" in result.output
    assert episodes.get_current_episode(tmp_path) == ep_id
