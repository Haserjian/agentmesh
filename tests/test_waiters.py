"""Tests for waiters + priority inheritance -- 6 scenarios."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentmesh import db
from agentmesh.claims import make_claim, normalize_path
from agentmesh.models import Agent, ResourceType
from agentmesh.waiters import register_wait, steal_resource


def _register(agent_id: str, data_dir: Path) -> None:
    db.register_agent(Agent(agent_id=agent_id, cwd="/tmp"), data_dir)


def test_register_wait(tmp_data_dir: Path) -> None:
    _register("a1", tmp_data_dir)
    _register("a2", tmp_data_dir)
    make_claim("a1", "PORT:3000", data_dir=tmp_data_dir)
    w = register_wait(
        "a2", "3000", priority=8, reason="critical test",
        resource_type=ResourceType.PORT, data_dir=tmp_data_dir,
    )
    assert w.waiter_id.startswith("wait_")
    assert w.priority == 8
    assert w.resource_path == "3000"


def test_priority_boost(tmp_data_dir: Path) -> None:
    _register("a1", tmp_data_dir)
    _register("a2", tmp_data_dir)
    make_claim("a1", "PORT:3000", priority=5, data_dir=tmp_data_dir)
    register_wait(
        "a2", "3000", priority=9, reason="high prio",
        resource_type=ResourceType.PORT, data_dir=tmp_data_dir,
    )
    # Holder's effective_priority should be boosted to 9
    active = db.list_claims(tmp_data_dir, agent_id="a1", active_only=True)
    port_claims = [c for c in active if c.resource_type == ResourceType.PORT]
    assert len(port_claims) == 1
    assert port_claims[0].effective_priority == 9


def test_steal_fail_active(tmp_data_dir: Path) -> None:
    """Cannot steal an active, fresh claim."""
    _register("a1", tmp_data_dir)
    _register("a2", tmp_data_dir)
    make_claim("a1", "/tmp/foo.py", ttl_s=3600, data_dir=tmp_data_dir)
    # Update heartbeat to now (fresh)
    db.update_heartbeat("a1", data_dir=tmp_data_dir)
    ok, msg = steal_resource(
        "a2", normalize_path("/tmp/foo.py"),
        reason="want it", stale_threshold_s=300,
        data_dir=tmp_data_dir,
    )
    assert not ok
    assert "still active" in msg


def test_steal_succeed_expired_ttl(tmp_data_dir: Path) -> None:
    """Can steal a claim whose TTL has expired."""
    _register("a1", tmp_data_dir)
    _register("a2", tmp_data_dir)
    make_claim("a1", "/tmp/foo.py", ttl_s=0, data_dir=tmp_data_dir)
    ok, msg = steal_resource(
        "a2", normalize_path("/tmp/foo.py"),
        reason="ttl gone", stale_threshold_s=300,
        data_dir=tmp_data_dir,
    )
    assert ok
    assert "ttl_expired" in msg


def test_steal_succeed_stale_heartbeat(tmp_data_dir: Path) -> None:
    """Can steal when holder heartbeat is stale."""
    _register("a1", tmp_data_dir)
    _register("a2", tmp_data_dir)
    make_claim("a1", "/tmp/foo.py", ttl_s=7200, data_dir=tmp_data_dir)
    # Make heartbeat stale (10 minutes ago)
    old_ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    db.update_heartbeat("a1", ts=old_ts, data_dir=tmp_data_dir)
    ok, msg = steal_resource(
        "a2", normalize_path("/tmp/foo.py"),
        reason="stale agent", stale_threshold_s=300,
        data_dir=tmp_data_dir,
    )
    assert ok
    assert "heartbeat_stale" in msg


def test_steal_no_claim(tmp_data_dir: Path) -> None:
    """Steal with no existing claim fails gracefully."""
    _register("a1", tmp_data_dir)
    ok, msg = steal_resource(
        "a1", normalize_path("/tmp/nothing.py"),
        reason="no one here", data_dir=tmp_data_dir,
    )
    assert not ok
    assert "no active claim" in msg
