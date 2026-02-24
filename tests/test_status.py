"""Tests for status dashboard."""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console

from agentmesh import db
from agentmesh.claims import make_claim
from agentmesh.messages import post
from agentmesh.models import Agent, Severity
from agentmesh.status import render_status


def _register(agent_id: str, data_dir: Path) -> None:
    db.register_agent(Agent(agent_id=agent_id, cwd="/tmp", display_name=agent_id), data_dir)


def test_status_json(tmp_data_dir: Path) -> None:
    _register("a1", tmp_data_dir)
    make_claim("a1", "/tmp/foo.py", data_dir=tmp_data_dir)
    post("a1", "test msg", data_dir=tmp_data_dir)
    result = render_status(data_dir=tmp_data_dir, as_json=True)
    data = json.loads(result)
    assert len(data["agents"]) == 1
    assert len(data["claims"]) == 1
    assert len(data["messages"]) == 1


def test_status_renders(tmp_data_dir: Path) -> None:
    _register("a1", tmp_data_dir)
    _register("a2", tmp_data_dir)
    make_claim("a1", "/tmp/foo.py", data_dir=tmp_data_dir)
    post("a2", "need help", severity=Severity.BLOCKER, data_dir=tmp_data_dir)
    c = Console(file=None, force_terminal=False, width=120)
    # Should not raise
    render_status(data_dir=tmp_data_dir, console=c)


def test_status_expires_stale_claims(tmp_data_dir: Path) -> None:
    """Status should not show expired claims."""
    _register("a1", tmp_data_dir)
    # Create claim with 0 TTL (already expired)
    make_claim("a1", "/tmp/stale.py", ttl_s=0, data_dir=tmp_data_dir)
    result = render_status(data_dir=tmp_data_dir, as_json=True)
    data = json.loads(result)
    assert len(data["claims"]) == 0
