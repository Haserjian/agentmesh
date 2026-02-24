"""MeshWatchdog -- heartbeat-based liveness detection for workers and spawns."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import db, events
from .models import AgentStatus, EventKind, TaskState


# Default: agent is stale after 5 minutes without heartbeat.
DEFAULT_STALE_THRESHOLD_S = 300

# Default: spawned worker is timed-out after 30 minutes (if no per-spawn timeout set).
DEFAULT_SPAWN_TIMEOUT_S = 1800


class WatchdogResult:
    """Result of a single watchdog scan pass."""

    def __init__(self) -> None:
        self.stale_agents: list[str] = []
        self.reaped_agents: list[str] = []
        self.aborted_tasks: list[str] = []
        self.harvested_spawns: list[str] = []
        self.timed_out_spawns: list[str] = []
        self.cost_exceeded_tasks: list[str] = []
        self.cost_exceeded_spawns: list[str] = []

    @property
    def clean(self) -> bool:
        return (
            not self.stale_agents
            and not self.reaped_agents
            and not self.aborted_tasks
            and not self.harvested_spawns
            and not self.timed_out_spawns
            and not self.cost_exceeded_tasks
        )

    def __repr__(self) -> str:
        return (
            f"WatchdogResult(stale={len(self.stale_agents)}, "
            f"reaped={len(self.reaped_agents)}, aborted={len(self.aborted_tasks)}, "
            f"harvested={len(self.harvested_spawns)}, timed_out={len(self.timed_out_spawns)}, "
            f"cost_exceeded={len(self.cost_exceeded_tasks)})"
        )


def check_stale_agents(
    stale_threshold_s: int = DEFAULT_STALE_THRESHOLD_S,
    data_dir: Path | None = None,
) -> list[str]:
    """Return agent_ids whose heartbeat is older than threshold."""
    agents = db.list_agents(data_dir, include_gone=False)
    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=stale_threshold_s)
    ).isoformat()
    return [
        a.agent_id for a in agents
        if a.status != AgentStatus.GONE and a.last_heartbeat < cutoff
    ]


def reap_agent(
    agent_id: str,
    data_dir: Path | None = None,
) -> None:
    """Mark an agent as gone and release all its active claims."""
    db.deregister_agent(agent_id, data_dir)
    db.release_claim(agent_id, release_all=True, data_dir=data_dir)


def abort_agent_tasks(
    agent_id: str,
    reason: str = "worker heartbeat stale",
    data_dir: Path | None = None,
) -> list[str]:
    """Abort all non-terminal tasks assigned to an agent. Returns aborted task_ids."""
    from . import orchestrator

    tasks = db.list_tasks(data_dir=data_dir, assigned_agent_id=agent_id)
    aborted = []
    for task in tasks:
        if task.state in orchestrator.TERMINAL_STATES:
            continue
        try:
            orchestrator.abort_task(
                task.task_id,
                reason=reason,
                agent_id=agent_id,
                data_dir=data_dir,
            )
            aborted.append(task.task_id)
        except orchestrator.TransitionError:
            pass
    return aborted


def _is_pid_alive(pid: int, expected_create_time: float = 0.0) -> bool:
    """Check if a process is still running.

    If *expected_create_time* is non-zero, also verify the running process
    was created at the same time (guards against PID reuse).
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass  # exists but we can't signal -- fall through to reuse check

    # PID exists.  If we have a fingerprint, verify it's the same process.
    if expected_create_time > 0:
        from . import spawner
        current_create_time = spawner._get_pid_create_time(pid)
        if current_create_time > 0 and abs(current_create_time - expected_create_time) > 2.0:
            # Different process reused this PID
            return False
    return True


def _is_spawn_timed_out(spawn_row: dict, default_timeout_s: int) -> bool:
    """Check if a spawn has exceeded its timeout."""
    timeout = spawn_row.get("timeout_s", 0) or default_timeout_s
    if timeout <= 0:
        return False
    started = spawn_row["started_at"]
    try:
        start_dt = datetime.fromisoformat(started)
    except (ValueError, TypeError):
        return False
    deadline = start_dt + timedelta(seconds=timeout)
    return datetime.now(timezone.utc) > deadline


def scan_spawns(
    default_timeout_s: int = DEFAULT_SPAWN_TIMEOUT_S,
    data_dir: Path | None = None,
) -> tuple[list[str], list[str]]:
    """Scan active spawns: auto-harvest exited ones, auto-abort timed-out ones.

    Returns (harvested_spawn_ids, timed_out_spawn_ids).
    """
    from . import spawner

    active_rows = db.list_spawns_db(active_only=True, data_dir=data_dir)
    harvested: list[str] = []
    timed_out: list[str] = []

    for row in active_rows:
        spawn_id = row["spawn_id"]

        # Re-read from DB to close TOCTOU gap (another process may have
        # harvested/aborted this spawn between our list query and now).
        fresh = db.get_spawn(spawn_id, data_dir)
        if fresh is None or fresh.get("ended_at", ""):
            continue

        pid = fresh["pid"]
        pid_create_ts = fresh.get("pid_started_at", 0.0) or 0.0
        alive = _is_pid_alive(pid, expected_create_time=pid_create_ts)

        if not alive:
            # Process exited -- auto-harvest
            try:
                spawner.harvest(spawn_id, cleanup_worktree=True, data_dir=data_dir)
                harvested.append(spawn_id)
            except spawner.SpawnError:
                pass  # Already harvested by concurrent CLI/watchdog
        elif _is_spawn_timed_out(fresh, default_timeout_s):
            # Still running but exceeded timeout -- abort
            try:
                spawner.abort(
                    spawn_id,
                    reason=f"watchdog timeout ({fresh.get('timeout_s', 0) or default_timeout_s}s)",
                    cleanup_worktree=True,
                    data_dir=data_dir,
                )
                timed_out.append(spawn_id)
            except spawner.SpawnError:
                pass  # Already aborted by concurrent CLI/watchdog

    return harvested, timed_out


def _task_budget_usd(meta: dict | None) -> float:
    if not isinstance(meta, dict):
        return 0.0
    raw = meta.get("max_cost_usd", 0.0)
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return val if val > 0 else 0.0


def _task_actual_cost_usd(task_id: str, data_dir: Path | None = None) -> float:
    total = 0.0
    for evt in events.read_events(data_dir):
        if evt.kind != EventKind.WORKER_DONE:
            continue
        payload = evt.payload if isinstance(evt.payload, dict) else {}
        if payload.get("task_id", "") != task_id:
            continue
        try:
            total += float(payload.get("cost_usd", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
    return total


def enforce_cost_budgets(
    data_dir: Path | None = None,
) -> tuple[list[str], list[str]]:
    """Abort running tasks/spawns whose cumulative cost exceeded budget."""
    from . import orchestrator, spawner, weaver

    exceeded_tasks: list[str] = []
    exceeded_spawns: list[str] = []

    running_tasks = db.list_tasks(data_dir=data_dir, state=TaskState.RUNNING, limit=500)
    if not running_tasks:
        return exceeded_tasks, exceeded_spawns

    active_rows = db.list_spawns_db(active_only=True, data_dir=data_dir)
    active_by_task: dict[str, list[str]] = {}
    for row in active_rows:
        active_by_task.setdefault(row.get("task_id", ""), []).append(row.get("spawn_id", ""))

    for task in running_tasks:
        budget = _task_budget_usd(task.meta)
        if budget <= 0:
            continue
        actual = _task_actual_cost_usd(task.task_id, data_dir=data_dir)
        if actual <= budget:
            continue

        spawn_ids = [s for s in active_by_task.get(task.task_id, []) if s]
        if spawn_ids:
            for spawn_id in spawn_ids:
                try:
                    spawner.abort(
                        spawn_id,
                        reason=f"cost budget exceeded ({actual:.4f} > {budget:.4f})",
                        cleanup_worktree=True,
                        data_dir=data_dir,
                    )
                    exceeded_spawns.append(spawn_id)
                except spawner.SpawnError:
                    pass
        else:
            try:
                orchestrator.abort_task(
                    task.task_id,
                    reason=f"cost budget exceeded ({actual:.4f} > {budget:.4f})",
                    agent_id=task.assigned_agent_id,
                    data_dir=data_dir,
                )
            except orchestrator.TransitionError:
                pass

        weaver.append_weave(
            trace_id=f"cost:{task.task_id}",
            episode_id=task.episode_id or None,
            data_dir=data_dir,
        )
        events.append_event(
            kind=EventKind.COST_EXCEEDED,
            payload={
                "task_id": task.task_id,
                "spawn_ids": spawn_ids,
                "budget_usd": budget,
                "actual_usd": actual,
            },
            data_dir=data_dir,
        )
        exceeded_tasks.append(task.task_id)

    return exceeded_tasks, exceeded_spawns


def scan(
    stale_threshold_s: int = DEFAULT_STALE_THRESHOLD_S,
    spawn_timeout_s: int = DEFAULT_SPAWN_TIMEOUT_S,
    data_dir: Path | None = None,
) -> WatchdogResult:
    """Run one watchdog pass: find stale agents, reap them, abort their tasks,
    and auto-harvest/abort orphaned spawns."""
    result = WatchdogResult()

    # -- Agent liveness --
    stale = check_stale_agents(stale_threshold_s, data_dir)
    result.stale_agents = stale

    for agent_id in stale:
        reap_agent(agent_id, data_dir)
        result.reaped_agents.append(agent_id)

        aborted = abort_agent_tasks(agent_id, data_dir=data_dir)
        result.aborted_tasks.extend(aborted)

    # -- Spawn liveness --
    harvested, timed_out = scan_spawns(spawn_timeout_s, data_dir)
    result.harvested_spawns = harvested
    result.timed_out_spawns = timed_out
    exceeded_tasks, exceeded_spawns = enforce_cost_budgets(data_dir=data_dir)
    result.cost_exceeded_tasks = exceeded_tasks
    result.cost_exceeded_spawns = exceeded_spawns

    if stale or harvested or timed_out or exceeded_tasks:
        events.append_event(
            kind=EventKind.GC,
            payload={
                "watchdog": "scan",
                "stale_agents": stale,
                "reaped": len(result.reaped_agents),
                "aborted_tasks": result.aborted_tasks,
                "harvested_spawns": harvested,
                "timed_out_spawns": timed_out,
                "cost_exceeded_tasks": exceeded_tasks,
                "cost_exceeded_spawns": exceeded_spawns,
            },
            data_dir=data_dir,
        )

    return result
