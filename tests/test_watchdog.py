"""Tests for the MeshWatchdog."""

import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentmesh import db, orchestrator, watchdog
from agentmesh.models import Agent, AgentStatus, TaskState


@pytest.fixture
def data_dir(tmp_path):
    """Fresh DB in a temp dir."""
    db.init_db(tmp_path)
    return tmp_path


def _stale_ts(seconds_ago: int = 600) -> str:
    """ISO timestamp N seconds in the past."""
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def _fresh_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


# -- Stale detection --

def test_fresh_agent_not_stale(data_dir):
    a = Agent(agent_id="fresh", last_heartbeat=_fresh_ts())
    db.register_agent(a, data_dir)
    stale = watchdog.check_stale_agents(data_dir=data_dir)
    assert stale == []


def test_stale_agent_detected(data_dir):
    a = Agent(agent_id="stale", last_heartbeat=_stale_ts(600))
    db.register_agent(a, data_dir)
    stale = watchdog.check_stale_agents(stale_threshold_s=300, data_dir=data_dir)
    assert "stale" in stale


def test_gone_agent_ignored(data_dir):
    a = Agent(agent_id="gone_one", status=AgentStatus.GONE, last_heartbeat=_stale_ts(600))
    db.register_agent(a, data_dir)
    stale = watchdog.check_stale_agents(data_dir=data_dir)
    assert stale == []


# -- Reap --

def test_reap_marks_gone(data_dir):
    a = Agent(agent_id="reap_me", last_heartbeat=_stale_ts())
    db.register_agent(a, data_dir)
    watchdog.reap_agent("reap_me", data_dir)
    agent = db.get_agent("reap_me", data_dir)
    assert agent.status == AgentStatus.GONE


def test_reap_releases_claims(data_dir):
    from agentmesh.models import Claim
    a = Agent(agent_id="claimer")
    db.register_agent(a, data_dir)

    now = _fresh_ts()
    c = Claim(
        claim_id="c1", agent_id="claimer", path="/tmp/f.py",
        created_at=now, expires_at="2099-01-01T00:00:00+00:00",
    )
    db.create_claim(c, data_dir)
    assert len(db.list_claims(data_dir, agent_id="claimer", active_only=True)) == 1

    watchdog.reap_agent("claimer", data_dir)
    assert len(db.list_claims(data_dir, agent_id="claimer", active_only=True)) == 0


# -- Task abort on reap --

def test_abort_agent_tasks(data_dir):
    a = Agent(agent_id="worker1")
    db.register_agent(a, data_dir)

    t = orchestrator.create_task("Doomed task", data_dir=data_dir)
    orchestrator.assign_task(t.task_id, "worker1", data_dir=data_dir)
    orchestrator.transition_task(t.task_id, TaskState.RUNNING, data_dir=data_dir)

    aborted = watchdog.abort_agent_tasks("worker1", data_dir=data_dir)
    assert t.task_id in aborted

    task = db.get_task(t.task_id, data_dir)
    assert task.state == TaskState.ABORTED


def test_abort_skips_terminal_tasks(data_dir):
    a = Agent(agent_id="worker2")
    db.register_agent(a, data_dir)

    t = orchestrator.create_task("Already done", data_dir=data_dir)
    orchestrator.assign_task(t.task_id, "worker2", data_dir=data_dir)
    orchestrator.transition_task(t.task_id, TaskState.RUNNING, data_dir=data_dir)
    orchestrator.abort_task(t.task_id, reason="earlier", data_dir=data_dir)

    aborted = watchdog.abort_agent_tasks("worker2", data_dir=data_dir)
    assert aborted == []


# -- Full scan --

def test_scan_full_pass(data_dir):
    # Fresh agent + stale agent
    db.register_agent(Agent(agent_id="alive", last_heartbeat=_fresh_ts()), data_dir)
    db.register_agent(Agent(agent_id="dead", last_heartbeat=_stale_ts(600)), data_dir)

    t = orchestrator.create_task("Orphaned", data_dir=data_dir)
    orchestrator.assign_task(t.task_id, "dead", data_dir=data_dir)
    orchestrator.transition_task(t.task_id, TaskState.RUNNING, data_dir=data_dir)

    result = watchdog.scan(stale_threshold_s=300, data_dir=data_dir)

    assert "dead" in result.stale_agents
    assert "alive" not in result.stale_agents
    assert "dead" in result.reaped_agents
    assert t.task_id in result.aborted_tasks
    assert not result.clean


def test_scan_clean_pass(data_dir):
    db.register_agent(Agent(agent_id="healthy", last_heartbeat=_fresh_ts()), data_dir)
    result = watchdog.scan(data_dir=data_dir)
    assert result.clean
    assert result.stale_agents == []
    assert result.reaped_agents == []
    assert result.aborted_tasks == []
