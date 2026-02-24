"""Tests for the MeshWatchdog."""

import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from agentmesh import db, events, orchestrator, watchdog
from agentmesh.models import Agent, AgentStatus, EventKind, TaskState


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


# -- Spawn scanning --

def _create_spawn_with_task(
    data_dir, spawn_id, pid, timeout_s=0, started_ago_s=0, pid_started_at=0.0,
    backend="claude_code",
):
    """Create a task in RUNNING state + a spawn record pointing at it."""
    a = Agent(agent_id="spawn_agent")
    try:
        db.register_agent(a, data_dir)
    except Exception:
        pass  # already registered
    task = orchestrator.create_task(f"Task for {spawn_id}", data_dir=data_dir)
    orchestrator.assign_task(task.task_id, "spawn_agent", branch="feat/test", data_dir=data_dir)
    orchestrator.transition_task(task.task_id, TaskState.RUNNING, agent_id="spawn_agent", data_dir=data_dir)
    started_at = (datetime.now(timezone.utc) - timedelta(seconds=started_ago_s)).isoformat()
    db.create_spawn(
        spawn_id=spawn_id, task_id=task.task_id, attempt_id="",
        agent_id="spawn_agent", pid=pid, worktree_path="/tmp/wt",
        branch="feat/test", episode_id="", context_hash="sha256:abc",
        started_at=started_at, output_path="/tmp/wt/.agentmesh/out.json",
        repo_cwd="/tmp", timeout_s=timeout_s, pid_started_at=pid_started_at,
        backend=backend,
        data_dir=data_dir,
    )
    return task.task_id


def test_scan_spawns_harvests_dead_process(data_dir):
    """Watchdog auto-harvests a spawn whose process has exited."""
    task_id = _create_spawn_with_task(data_dir, "spawn_dead", pid=99998)

    # Process is dead (ProcessLookupError)
    with patch("agentmesh.watchdog._is_pid_alive", return_value=False):
        with patch("agentmesh.spawner.remove_worktree", return_value=(True, "")):
            harvested, timed_out = watchdog.scan_spawns(data_dir=data_dir)

    assert "spawn_dead" in harvested
    assert timed_out == []
    # Task should be ABORTED (no output file -> failure path in harvest)
    t = db.get_task(task_id, data_dir)
    assert t.state == TaskState.ABORTED


def test_scan_spawns_unknown_backend_does_not_crash(data_dir):
    """Unknown backend in DB should fail closed, not crash watchdog scan."""
    task_id = _create_spawn_with_task(
        data_dir, "spawn_unknown_backend", pid=99987, backend="missing_backend",
    )

    with patch("agentmesh.watchdog._is_pid_alive", return_value=False):
        with patch("agentmesh.spawner.remove_worktree", return_value=(True, "")):
            harvested, timed_out = watchdog.scan_spawns(data_dir=data_dir)

    assert "spawn_unknown_backend" in harvested
    assert timed_out == []
    t = db.get_task(task_id, data_dir)
    assert t.state == TaskState.ABORTED


def test_scan_spawns_aborts_timed_out(data_dir):
    """Watchdog auto-aborts a spawn that exceeded its timeout."""
    task_id = _create_spawn_with_task(
        data_dir, "spawn_slow", pid=99997, timeout_s=60, started_ago_s=120,
    )

    # Process is alive but timed out
    with patch("agentmesh.watchdog._is_pid_alive", return_value=True):
        with patch("agentmesh.spawner.remove_worktree", return_value=(True, "")):
            with patch("os.kill"):  # for _terminate_pid
                harvested, timed_out = watchdog.scan_spawns(
                    default_timeout_s=1800, data_dir=data_dir,
                )

    assert harvested == []
    assert "spawn_slow" in timed_out
    t = db.get_task(task_id, data_dir)
    assert t.state == TaskState.ABORTED


def test_scan_spawns_skips_alive_within_timeout(data_dir):
    """Watchdog leaves running spawns within timeout alone."""
    _create_spawn_with_task(
        data_dir, "spawn_ok", pid=99996, timeout_s=3600, started_ago_s=10,
    )

    with patch("agentmesh.watchdog._is_pid_alive", return_value=True):
        harvested, timed_out = watchdog.scan_spawns(data_dir=data_dir)

    assert harvested == []
    assert timed_out == []


def test_scan_includes_spawn_results(data_dir):
    """Full scan() includes spawn harvesting/aborting in results."""
    db.register_agent(Agent(agent_id="spawn_agent", last_heartbeat=_fresh_ts()), data_dir)
    task_id = _create_spawn_with_task(data_dir, "spawn_full", pid=99995)

    with patch("agentmesh.watchdog._is_pid_alive", return_value=False):
        with patch("agentmesh.spawner.remove_worktree", return_value=(True, "")):
            result = watchdog.scan(data_dir=data_dir)

    assert "spawn_full" in result.harvested_spawns
    assert not result.clean


def test_scan_aborts_when_cost_budget_exceeded(data_dir):
    """Watchdog aborts running spawn when cumulative task cost exceeds budget."""
    task_id = _create_spawn_with_task(data_dir, "spawn_budget", pid=99994)
    db.update_task(task_id, data_dir=data_dir, meta={"max_cost_usd": 1.0})
    events.append_event(
        EventKind.WORKER_DONE,
        payload={"task_id": task_id, "spawn_id": "previous", "cost_usd": 1.5},
        data_dir=data_dir,
    )

    with patch("agentmesh.watchdog._is_pid_alive", return_value=True):
        with patch("agentmesh.spawner.remove_worktree", return_value=(True, "")):
            with patch("os.kill"):
                result = watchdog.scan(data_dir=data_dir)

    assert task_id in result.cost_exceeded_tasks
    t = db.get_task(task_id, data_dir)
    assert t.state == TaskState.ABORTED


# -- PID-reuse protection --

def test_pid_reuse_detected_as_dead(data_dir):
    """If a different process reused the PID, watchdog treats spawn as dead."""
    original_create_time = 1700000000.0  # stored at spawn time
    _create_spawn_with_task(
        data_dir, "spawn_reuse", pid=99994, pid_started_at=original_create_time,
    )

    # os.kill succeeds (PID exists), but create time doesn't match
    different_create_time = 1700099999.0  # new process reused PID
    with patch("os.kill"):  # PID exists
        with patch(
            "agentmesh.spawner._get_pid_create_time", return_value=different_create_time,
        ):
            alive = watchdog._is_pid_alive(99994, expected_create_time=original_create_time)

    assert alive is False


def test_pid_same_process_detected_as_alive(data_dir):
    """If the same process still owns the PID, watchdog treats spawn as alive."""
    create_time = 1700000000.0
    with patch("os.kill"):  # PID exists
        with patch(
            "agentmesh.spawner._get_pid_create_time", return_value=create_time,
        ):
            alive = watchdog._is_pid_alive(99993, expected_create_time=create_time)

    assert alive is True


def test_pid_reuse_no_fingerprint_falls_back(data_dir):
    """Without a stored create time, _is_pid_alive falls back to PID-only check."""
    with patch("os.kill"):  # PID exists
        alive = watchdog._is_pid_alive(99992, expected_create_time=0.0)
    assert alive is True


# -- Race idempotency --

def test_scan_spawns_idempotent_already_harvested(data_dir):
    """If another process harvested a spawn between list and act, scan skips it."""
    task_id = _create_spawn_with_task(data_dir, "spawn_race", pid=99991)

    # Simulate: spawn was harvested between list_spawns_db and the per-row re-read.
    # Mark it as ended in DB before scan_spawns iterates.
    db.update_spawn("spawn_race", ended_at=_fresh_ts(), outcome="success", data_dir=data_dir)

    with patch("agentmesh.watchdog._is_pid_alive", return_value=False):
        harvested, timed_out = watchdog.scan_spawns(data_dir=data_dir)

    # Should skip it because the re-read sees ended_at is set
    assert "spawn_race" not in harvested
    assert timed_out == []
