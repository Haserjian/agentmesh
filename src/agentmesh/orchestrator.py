"""Orchestrator -- atomic task state machine with receipt emission."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from . import db, events, weaver
from .models import (
    Attempt,
    EventKind,
    Task,
    TaskState,
    _now,
)

# Valid state transitions. Key = current state, value = set of allowed next states.
VALID_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.PLANNED: {TaskState.ASSIGNED, TaskState.ABORTED},
    TaskState.ASSIGNED: {TaskState.RUNNING, TaskState.ABORTED},
    TaskState.RUNNING: {TaskState.PR_OPEN, TaskState.ABORTED},
    TaskState.PR_OPEN: {TaskState.CI_PASS, TaskState.ABORTED},
    TaskState.CI_PASS: {TaskState.REVIEW_PASS, TaskState.ABORTED},
    TaskState.REVIEW_PASS: {TaskState.MERGED, TaskState.ABORTED},
    TaskState.MERGED: set(),
    TaskState.ABORTED: set(),
}

# Terminal states -- no further transitions allowed.
TERMINAL_STATES = {TaskState.MERGED, TaskState.ABORTED}


class TransitionError(Exception):
    """Raised when a state transition is invalid."""


def create_task(
    title: str,
    description: str = "",
    episode_id: str = "",
    parent_task_id: str = "",
    meta: dict[str, Any] | None = None,
    data_dir: Path | None = None,
) -> Task:
    """Create a new task in PLANNED state and emit a receipt."""
    task_id = f"task_{uuid.uuid4().hex[:12]}"
    now = _now()
    task = Task(
        task_id=task_id,
        title=title,
        description=description,
        state=TaskState.PLANNED,
        episode_id=episode_id,
        parent_task_id=parent_task_id,
        meta=meta or {},
        created_at=now,
        updated_at=now,
    )
    db.create_task(task, data_dir)

    # Receipt: weave event for task creation
    weaver.append_weave(
        trace_id=task_id,
        episode_id=episode_id or None,
        data_dir=data_dir,
    )

    # Event log entry
    events.append_event(
        kind=EventKind.TASK_TRANSITION,
        payload={
            "task_id": task_id,
            "from_state": "",
            "to_state": TaskState.PLANNED.value,
            "title": title,
        },
        data_dir=data_dir,
    )

    return task


def transition_task(
    task_id: str,
    to_state: TaskState,
    agent_id: str = "",
    reason: str = "",
    data_dir: Path | None = None,
    **update_kwargs: Any,
) -> Task:
    """Atomically transition a task to a new state.

    Validates the transition, updates the DB, emits a weave receipt
    and an event log entry. Returns the updated Task.

    Raises TransitionError if the transition is invalid.
    """
    task = db.get_task(task_id, data_dir)
    if task is None:
        raise TransitionError(f"Task {task_id} not found")

    current = task.state
    if current in TERMINAL_STATES:
        raise TransitionError(
            f"Task {task_id} is in terminal state {current.value}"
        )

    allowed = VALID_TRANSITIONS.get(current, set())
    if to_state not in allowed:
        raise TransitionError(
            f"Cannot transition {task_id} from {current.value} to {to_state.value}. "
            f"Allowed: {sorted(s.value for s in allowed)}"
        )

    # Apply the state change + any extra fields
    db.update_task(task_id, data_dir=data_dir, state=to_state, **update_kwargs)

    # Emit receipt to weave (append-only, hash-chained)
    weaver.append_weave(
        trace_id=task_id,
        episode_id=task.episode_id or None,
        data_dir=data_dir,
    )

    # Emit event log entry (operational telemetry)
    events.append_event(
        kind=EventKind.TASK_TRANSITION,
        agent_id=agent_id,
        payload={
            "task_id": task_id,
            "from_state": current.value,
            "to_state": to_state.value,
            "reason": reason,
        },
        data_dir=data_dir,
    )

    # Re-fetch to return latest state
    updated = db.get_task(task_id, data_dir)
    assert updated is not None
    return updated


def assign_task(
    task_id: str,
    agent_id: str,
    branch: str = "",
    data_dir: Path | None = None,
) -> Task:
    """Assign a PLANNED task to an agent. Creates an attempt record."""
    task = transition_task(
        task_id,
        TaskState.ASSIGNED,
        agent_id=agent_id,
        reason=f"assigned to {agent_id}",
        data_dir=data_dir,
        assigned_agent_id=agent_id,
        branch=branch,
    )

    # Create attempt record
    attempt_id = f"att_{uuid.uuid4().hex[:12]}"
    existing = db.list_attempts(task_id, data_dir)
    attempt = Attempt(
        attempt_id=attempt_id,
        task_id=task_id,
        agent_id=agent_id,
        attempt_number=len(existing) + 1,
    )
    db.create_attempt(attempt, data_dir)

    # Emit spawn event
    events.append_event(
        kind=EventKind.WORKER_SPAWN,
        agent_id=agent_id,
        payload={
            "task_id": task_id,
            "attempt_id": attempt_id,
            "branch": branch,
        },
        data_dir=data_dir,
    )

    return task


def abort_task(
    task_id: str,
    reason: str = "",
    agent_id: str = "",
    data_dir: Path | None = None,
) -> Task:
    """Abort a task from any non-terminal state."""
    return transition_task(
        task_id,
        TaskState.ABORTED,
        agent_id=agent_id,
        reason=reason or "aborted",
        data_dir=data_dir,
    )


def complete_task(
    task_id: str,
    agent_id: str = "",
    data_dir: Path | None = None,
) -> Task:
    """Mark a REVIEW_PASS task as MERGED."""
    task = transition_task(
        task_id,
        TaskState.MERGED,
        agent_id=agent_id,
        reason="merged",
        data_dir=data_dir,
    )

    # End the latest attempt with success
    attempts = db.list_attempts(task_id, data_dir)
    if attempts:
        latest = attempts[-1]
        if not latest.ended_at:
            db.end_attempt(latest.attempt_id, outcome="success", data_dir=data_dir)

    events.append_event(
        kind=EventKind.WORKER_DONE,
        agent_id=agent_id,
        payload={"task_id": task_id, "outcome": "success"},
        data_dir=data_dir,
    )

    return task
