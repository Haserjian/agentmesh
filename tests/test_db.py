"""Tests for SQLite database layer."""

from __future__ import annotations

from pathlib import Path

from agentmesh import db
from agentmesh.models import Agent, AgentKind, AgentStatus, _now


def test_init_db_creates_tables(tmp_data_dir: Path) -> None:
    conn = db.get_connection(tmp_data_dir)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    conn.close()
    names = [r["name"] for r in tables]
    assert "agents" in names
    assert "claims" in names
    assert "messages" in names
    assert "capsules" in names


def test_wal_mode(tmp_data_dir: Path) -> None:
    conn = db.get_connection(tmp_data_dir)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode == "wal"


def test_register_and_get_agent(tmp_data_dir: Path) -> None:
    now = _now()
    a = Agent(
        agent_id="test01", kind=AgentKind.CLAUDE_CODE, display_name="Test",
        cwd="/tmp", pid=1234, status=AgentStatus.IDLE,
        registered_at=now, last_heartbeat=now,
    )
    db.register_agent(a, tmp_data_dir)
    got = db.get_agent("test01", tmp_data_dir)
    assert got is not None
    assert got.agent_id == "test01"
    assert got.display_name == "Test"


def test_deregister_agent(tmp_data_dir: Path) -> None:
    a = Agent(agent_id="test02", cwd="/tmp")
    db.register_agent(a, tmp_data_dir)
    ok = db.deregister_agent("test02", tmp_data_dir)
    assert ok
    got = db.get_agent("test02", tmp_data_dir)
    assert got is not None
    assert got.status == AgentStatus.GONE


def test_list_agents_excludes_gone(tmp_data_dir: Path) -> None:
    db.register_agent(Agent(agent_id="a1", cwd="/tmp"), tmp_data_dir)
    db.register_agent(Agent(agent_id="a2", cwd="/tmp"), tmp_data_dir)
    db.deregister_agent("a2", tmp_data_dir)
    active = db.list_agents(tmp_data_dir)
    assert len(active) == 1
    assert active[0].agent_id == "a1"
    all_agents = db.list_agents(tmp_data_dir, include_gone=True)
    assert len(all_agents) == 2


def test_update_heartbeat(tmp_data_dir: Path) -> None:
    db.register_agent(Agent(agent_id="a3", cwd="/tmp"), tmp_data_dir)
    ok = db.update_heartbeat("a3", AgentStatus.BUSY, data_dir=tmp_data_dir)
    assert ok
    got = db.get_agent("a3", tmp_data_dir)
    assert got is not None
    assert got.status == AgentStatus.BUSY
