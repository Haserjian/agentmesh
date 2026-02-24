"""CLI tests for the orchestrator sub-app."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from agentmesh import db
from agentmesh.cli import app
from agentmesh.models import Agent

runner = CliRunner()


def _invoke(args: list[str], tmp_path):
    return runner.invoke(app, ["--data-dir", str(tmp_path)] + args)


def _setup(tmp_path):
    db.init_db(tmp_path)
    a = Agent(agent_id="agent_cli", cwd="/tmp")
    db.register_agent(a, tmp_path)


# -- orch create --

def test_orch_create(tmp_path):
    _setup(tmp_path)
    result = _invoke(["orch", "create", "--title", "Fix bug"], tmp_path)
    assert result.exit_code == 0
    assert "task_" in result.output
    assert "planned" in result.output


def test_orch_create_json(tmp_path):
    _setup(tmp_path)
    result = _invoke(["orch", "create", "--title", "JSON task", "--json"], tmp_path)
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["state"] == "planned"
    assert data["task_id"].startswith("task_")


# -- orch list --

def test_orch_list(tmp_path):
    _setup(tmp_path)
    _invoke(["orch", "create", "--title", "T1"], tmp_path)
    _invoke(["orch", "create", "--title", "T2"], tmp_path)
    result = _invoke(["orch", "list"], tmp_path)
    assert result.exit_code == 0
    assert "T1" in result.output
    assert "T2" in result.output


# -- orch assign + show --

def test_orch_assign_and_show(tmp_path):
    _setup(tmp_path)
    create_result = _invoke(["orch", "create", "--title", "Assign me", "--json"], tmp_path)
    task_id = json.loads(create_result.output)["task_id"]

    assign_result = _invoke(["orch", "assign", task_id, "--agent", "agent_cli", "--branch", "feat/x"], tmp_path)
    assert assign_result.exit_code == 0
    assert "assigned" in assign_result.output

    show_result = _invoke(["orch", "show", task_id, "--json"], tmp_path)
    assert show_result.exit_code == 0
    data = json.loads(show_result.output)
    assert data["state"] == "assigned"
    assert data["assigned_agent_id"] == "agent_cli"
    assert len(data["attempts"]) == 1


# -- orch advance + abort --

def test_orch_advance_and_abort(tmp_path):
    _setup(tmp_path)
    create_result = _invoke(["orch", "create", "--title", "Advance me", "--json"], tmp_path)
    task_id = json.loads(create_result.output)["task_id"]

    _invoke(["orch", "assign", task_id, "--agent", "agent_cli"], tmp_path)

    advance_result = _invoke(["orch", "advance", task_id, "--to", "running"], tmp_path)
    assert advance_result.exit_code == 0
    assert "running" in advance_result.output

    abort_result = _invoke(["orch", "abort", task_id, "--reason", "test abort"], tmp_path)
    assert abort_result.exit_code == 0
    assert "Aborted" in abort_result.output


# -- invalid transition --

def test_orch_advance_invalid(tmp_path):
    _setup(tmp_path)
    create_result = _invoke(["orch", "create", "--title", "Bad transition", "--json"], tmp_path)
    task_id = json.loads(create_result.output)["task_id"]

    result = _invoke(["orch", "advance", task_id, "--to", "running"], tmp_path)
    assert result.exit_code == 1
    assert "Cannot transition" in result.output
