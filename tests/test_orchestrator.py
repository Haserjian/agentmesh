"""Tests for the orchestrator state machine."""

import pytest
from pathlib import Path

from agentmesh import db, orchestrator
from agentmesh.models import Agent, TaskState, _now


@pytest.fixture
def data_dir(tmp_path):
    """Fresh DB in a temp dir."""
    db.init_db(tmp_path)
    return tmp_path


@pytest.fixture
def agent(data_dir):
    """Register a test agent."""
    a = Agent(agent_id="agent_test", cwd="/tmp")
    db.register_agent(a, data_dir)
    return a


# -- Task creation --

def test_create_task(data_dir):
    task = orchestrator.create_task("Fix bug", description="details", data_dir=data_dir)
    assert task.task_id.startswith("task_")
    assert task.state == TaskState.PLANNED
    assert task.title == "Fix bug"

    # Persisted in DB
    fetched = db.get_task(task.task_id, data_dir)
    assert fetched is not None
    assert fetched.state == TaskState.PLANNED


def test_create_task_emits_weave(data_dir):
    task = orchestrator.create_task("Test weave", data_dir=data_dir)
    weave_events = db.list_weave_events(data_dir)
    assert len(weave_events) >= 1
    assert weave_events[-1].trace_id == task.task_id


def test_create_task_emits_event(data_dir):
    from agentmesh.events import read_events
    task = orchestrator.create_task("Test event", data_dir=data_dir)
    evts = read_events(data_dir)
    transition_evts = [e for e in evts if e.kind == "TASK_TRANSITION"]
    assert len(transition_evts) >= 1
    last = transition_evts[-1]
    assert last.payload["task_id"] == task.task_id
    assert last.payload["to_state"] == "planned"


# -- Valid transitions --

def test_full_lifecycle(data_dir, agent):
    """Walk through the entire happy path: PLANNED -> ... -> MERGED."""
    task = orchestrator.create_task("Feature X", data_dir=data_dir)

    # PLANNED -> ASSIGNED
    task = orchestrator.assign_task(task.task_id, agent.agent_id, branch="feat-x", data_dir=data_dir)
    assert task.state == TaskState.ASSIGNED
    assert task.assigned_agent_id == agent.agent_id

    # Attempt created
    attempts = db.list_attempts(task.task_id, data_dir)
    assert len(attempts) == 1
    assert attempts[0].agent_id == agent.agent_id

    # ASSIGNED -> RUNNING
    task = orchestrator.transition_task(task.task_id, TaskState.RUNNING, data_dir=data_dir)
    assert task.state == TaskState.RUNNING

    # RUNNING -> PR_OPEN
    task = orchestrator.transition_task(
        task.task_id, TaskState.PR_OPEN, data_dir=data_dir,
        pr_url="https://github.com/test/repo/pull/1",
    )
    assert task.state == TaskState.PR_OPEN

    # PR_OPEN -> CI_PASS
    task = orchestrator.transition_task(task.task_id, TaskState.CI_PASS, data_dir=data_dir)
    assert task.state == TaskState.CI_PASS

    # CI_PASS -> REVIEW_PASS
    task = orchestrator.transition_task(task.task_id, TaskState.REVIEW_PASS, data_dir=data_dir)
    assert task.state == TaskState.REVIEW_PASS

    # REVIEW_PASS -> MERGED
    task = orchestrator.complete_task(task.task_id, agent_id=agent.agent_id, data_dir=data_dir)
    assert task.state == TaskState.MERGED

    # Attempt ended with success
    attempts = db.list_attempts(task.task_id, data_dir)
    assert attempts[-1].outcome == "success"


# -- Invalid transitions --

def test_invalid_transition_rejected(data_dir):
    task = orchestrator.create_task("Bad path", data_dir=data_dir)
    with pytest.raises(orchestrator.TransitionError, match="Cannot transition"):
        orchestrator.transition_task(task.task_id, TaskState.RUNNING, data_dir=data_dir)


def test_transition_from_terminal_rejected(data_dir, agent):
    task = orchestrator.create_task("Terminal", data_dir=data_dir)
    orchestrator.abort_task(task.task_id, reason="test", data_dir=data_dir)
    with pytest.raises(orchestrator.TransitionError, match="terminal state"):
        orchestrator.transition_task(task.task_id, TaskState.PLANNED, data_dir=data_dir)


def test_transition_nonexistent_task(data_dir):
    with pytest.raises(orchestrator.TransitionError, match="not found"):
        orchestrator.transition_task("task_nonexistent", TaskState.ASSIGNED, data_dir=data_dir)


# -- Abort --

def test_abort_from_running(data_dir, agent):
    task = orchestrator.create_task("Will abort", data_dir=data_dir)
    orchestrator.assign_task(task.task_id, agent.agent_id, data_dir=data_dir)
    orchestrator.transition_task(task.task_id, TaskState.RUNNING, data_dir=data_dir)

    task = orchestrator.abort_task(task.task_id, reason="timeout", data_dir=data_dir)
    assert task.state == TaskState.ABORTED


def test_abort_from_planned(data_dir):
    task = orchestrator.create_task("Never started", data_dir=data_dir)
    task = orchestrator.abort_task(task.task_id, reason="cancelled", data_dir=data_dir)
    assert task.state == TaskState.ABORTED


# -- Receipt emission --

def test_each_transition_emits_weave(data_dir, agent):
    """Every transition should add a weave event."""
    initial_count = len(db.list_weave_events(data_dir))
    task = orchestrator.create_task("Weave test", data_dir=data_dir)
    after_create = len(db.list_weave_events(data_dir))
    assert after_create == initial_count + 1

    orchestrator.assign_task(task.task_id, agent.agent_id, data_dir=data_dir)
    after_assign = len(db.list_weave_events(data_dir))
    assert after_assign == after_create + 1

    orchestrator.transition_task(task.task_id, TaskState.RUNNING, data_dir=data_dir)
    after_running = len(db.list_weave_events(data_dir))
    assert after_running == after_assign + 1


# -- Listing --

def test_list_tasks_by_state(data_dir, agent):
    t1 = orchestrator.create_task("T1", data_dir=data_dir)
    t2 = orchestrator.create_task("T2", data_dir=data_dir)
    orchestrator.assign_task(t1.task_id, agent.agent_id, data_dir=data_dir)

    planned = db.list_tasks(data_dir=data_dir, state=TaskState.PLANNED)
    assert len(planned) == 1
    assert planned[0].task_id == t2.task_id

    assigned = db.list_tasks(data_dir=data_dir, state=TaskState.ASSIGNED)
    assert len(assigned) == 1
    assert assigned[0].task_id == t1.task_id


def test_list_tasks_by_agent(data_dir, agent):
    t1 = orchestrator.create_task("T1", data_dir=data_dir)
    orchestrator.assign_task(t1.task_id, agent.agent_id, data_dir=data_dir)
    orchestrator.create_task("T2", data_dir=data_dir)

    mine = db.list_tasks(data_dir=data_dir, assigned_agent_id=agent.agent_id)
    assert len(mine) == 1
    assert mine[0].task_id == t1.task_id
