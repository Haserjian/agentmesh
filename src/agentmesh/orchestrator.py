"""Orchestrator -- atomic task state machine with receipt emission."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from . import assay_bridge, db, events, orch_control, weaver
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


DEPENDENCY_READY_STATES = {
    TaskState.PR_OPEN,
    TaskState.CI_PASS,
    TaskState.REVIEW_PASS,
    TaskState.MERGED,
}


def _normalize_depends_on(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        value = raw.strip()
        return [value] if value else []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        value = str(item).strip()
        if value and value not in out:
            out.append(value)
    return out


def _task_depends_on(task: Task) -> list[str]:
    if not isinstance(task.meta, dict):
        return []
    return _normalize_depends_on(task.meta.get("depends_on", []))


def _build_dependency_graph(tasks: list[Task]) -> dict[str, list[str]]:
    graph: dict[str, list[str]] = {}
    for task in tasks:
        graph[task.task_id] = _task_depends_on(task)
    return graph


def _find_cycle(graph: dict[str, list[str]]) -> list[str]:
    color: dict[str, int] = {}
    stack: list[str] = []

    def dfs(node: str) -> list[str]:
        color[node] = 1
        stack.append(node)
        for nxt in graph.get(node, []):
            if nxt not in graph:
                continue
            state = color.get(nxt, 0)
            if state == 0:
                cyc = dfs(nxt)
                if cyc:
                    return cyc
            elif state == 1:
                idx = stack.index(nxt)
                return stack[idx:] + [nxt]
        stack.pop()
        color[node] = 2
        return []

    for n in graph:
        if color.get(n, 0) == 0:
            cyc = dfs(n)
            if cyc:
                return cyc
    return []


def _validate_task_graph(tasks: list[Task]) -> None:
    graph = _build_dependency_graph(tasks)
    cycle = _find_cycle(graph)
    if cycle:
        rendered = " -> ".join(cycle)
        raise TransitionError(f"Task dependency cycle detected: {rendered}")


def _dependency_blockers(task: Task, data_dir: Path | None) -> list[str]:
    blockers: list[str] = []
    for dep_task_id in _task_depends_on(task):
        dep = db.get_task(dep_task_id, data_dir)
        if dep is None:
            blockers.append(f"{dep_task_id}:missing")
            continue
        if dep.state == TaskState.ABORTED:
            blockers.append(f"{dep_task_id}:aborted")
            continue
        if dep.state not in DEPENDENCY_READY_STATES:
            blockers.append(f"{dep_task_id}:{dep.state.value}")
    return blockers


def create_task(
    title: str,
    description: str = "",
    episode_id: str = "",
    parent_task_id: str = "",
    depends_on: list[str] | None = None,
    meta: dict[str, Any] | None = None,
    data_dir: Path | None = None,
) -> Task:
    """Create a new task in PLANNED state and emit a receipt."""
    task_id = f"task_{uuid.uuid4().hex[:12]}"
    now = _now()
    task_meta = dict(meta or {})
    dep_list = _normalize_depends_on(depends_on if depends_on is not None else task_meta.get("depends_on", []))
    if task_id in dep_list:
        raise TransitionError(f"Task {task_id} cannot depend on itself")
    if dep_list:
        task_meta["depends_on"] = dep_list
    else:
        task_meta.pop("depends_on", None)

    existing_tasks = db.list_tasks(data_dir=data_dir, limit=5000)
    known = {t.task_id for t in existing_tasks}
    missing = [dep for dep in dep_list if dep not in known]
    if missing:
        raise TransitionError(
            f"Task {task_id} has unknown dependencies: {', '.join(sorted(missing))}"
        )

    task = Task(
        task_id=task_id,
        title=title,
        description=description,
        state=TaskState.PLANNED,
        episode_id=episode_id,
        parent_task_id=parent_task_id,
        meta=task_meta,
        created_at=now,
        updated_at=now,
    )
    _validate_task_graph(existing_tasks + [task])
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
    if to_state == TaskState.MERGED and orch_control.is_merges_locked(data_dir):
        raise TransitionError(
            f"Cannot transition {task_id} to merged: merge transitions are locked"
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
    current = db.get_task(task_id, data_dir)
    if current is None:
        raise TransitionError(f"Task {task_id} not found")

    blockers = _dependency_blockers(current, data_dir)
    if blockers:
        raise TransitionError(
            "Cannot assign task with unresolved dependencies: "
            + ", ".join(blockers)
        )

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


def set_task_dependencies(
    task_id: str,
    depends_on: list[str],
    data_dir: Path | None = None,
) -> Task:
    """Set dependency edges for an existing task, validating DAG constraints."""
    task = db.get_task(task_id, data_dir)
    if task is None:
        raise TransitionError(f"Task {task_id} not found")
    if task.state in TERMINAL_STATES:
        raise TransitionError(
            f"Cannot update dependencies for terminal task {task_id} ({task.state.value})"
        )

    dep_list = _normalize_depends_on(depends_on)
    if task_id in dep_list:
        raise TransitionError(f"Task {task_id} cannot depend on itself")

    tasks = db.list_tasks(data_dir=data_dir, limit=5000)
    known = {t.task_id for t in tasks}
    missing = [dep for dep in dep_list if dep not in known]
    if missing:
        raise TransitionError(
            f"Task {task_id} has unknown dependencies: {', '.join(sorted(missing))}"
        )

    updated_meta = dict(task.meta or {})
    if dep_list:
        updated_meta["depends_on"] = dep_list
    else:
        updated_meta.pop("depends_on", None)

    updated_task = task.model_copy(update={"meta": updated_meta, "updated_at": _now()})
    rewritten = [updated_task if t.task_id == task_id else t for t in tasks]
    _validate_task_graph(rewritten)

    db.update_task(task_id, data_dir=data_dir, meta=updated_meta)
    latest = db.get_task(task_id, data_dir)
    assert latest is not None
    return latest


def abort_task(
    task_id: str,
    reason: str = "",
    agent_id: str = "",
    data_dir: Path | None = None,
) -> Task:
    """Abort a task from any non-terminal state."""
    task = transition_task(
        task_id,
        TaskState.ABORTED,
        agent_id=agent_id,
        reason=reason or "aborted",
        data_dir=data_dir,
    )

    assay_bridge.emit_bridge_event(
        task_id=task_id,
        terminal_state="ABORTED",
        agent_id=agent_id,
        data_dir=data_dir,
    )

    return task


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

    assay_bridge.emit_bridge_event(
        task_id=task_id,
        terminal_state="MERGED",
        agent_id=agent_id,
        data_dir=data_dir,
    )

    return task
