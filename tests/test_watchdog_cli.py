"""CLI tests for the watchdog command."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from agentmesh import db
from agentmesh.cli import app
from agentmesh.models import Agent, AgentStatus

runner = CliRunner()


def _invoke(args: list[str], tmp_path):
    return runner.invoke(app, ["--data-dir", str(tmp_path)] + args)


def test_watchdog_clean(tmp_path):
    """No agents = clean scan."""
    db.init_db(tmp_path)
    result = _invoke(["watchdog"], tmp_path)
    assert result.exit_code == 0
    assert "Clean" in result.output


def test_watchdog_json(tmp_path):
    """JSON output with no stale agents."""
    db.init_db(tmp_path)
    result = _invoke(["watchdog", "--json"], tmp_path)
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["clean"] is True
    assert data["stale_agents"] == []
