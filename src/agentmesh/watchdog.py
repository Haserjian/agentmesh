"""MeshWatchdog -- heartbeat-based liveness detection for workers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import db, events
from .models import AgentStatus, EventKind, TaskState


# Default: agent is stale after 5 minutes without heartbeat.
DEFAULT_STALE_THRESHOLD_S = 300


class WatchdogResult:
    """Result of a single watchdog scan pass."""

    def __init__(self) -> None:
        self.stale_agents: list[str] = []
        self.reaped_agents: list[str] = []
        self.aborted_tasks: list[str] = []

    @property
    def clean(self) -> bool:
        return not self.stale_agents and not self.reaped_agents and not self.aborted_tasks

    def __repr__(self) -> str:
        return (
            f"WatchdogResult(stale={len(self.stale_agents)}, "
            f"reaped={len(self.reaped_agents)}, aborted={len(self.aborted_tasks)})"
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


def scan(
    stale_threshold_s: int = DEFAULT_STALE_THRESHOLD_S,
    data_dir: Path | None = None,
) -> WatchdogResult:
    """Run one watchdog pass: find stale agents, reap them, abort their tasks."""
    result = WatchdogResult()

    stale = check_stale_agents(stale_threshold_s, data_dir)
    result.stale_agents = stale

    for agent_id in stale:
        reap_agent(agent_id, data_dir)
        result.reaped_agents.append(agent_id)

        aborted = abort_agent_tasks(agent_id, data_dir=data_dir)
        result.aborted_tasks.extend(aborted)

    if stale:
        events.append_event(
            kind=EventKind.GC,
            payload={
                "watchdog": "scan",
                "stale_agents": stale,
                "reaped": len(result.reaped_agents),
                "aborted_tasks": result.aborted_tasks,
            },
            data_dir=data_dir,
        )

    return result
