"""Evidence Wire Protocol v0 compatibility tests.

Validates that the _ewp_* identity envelope fields are:
  - Present in ASSAY_RECEIPT payloads (T1)
  - Propagated with episode_id when available (T2)
  - Preserved through round-trip (unknown fields not dropped) (T7)
  - Not required (absent fields don't break anything) (T8)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agentmesh import db, events, orchestrator
from agentmesh.assay_bridge import emit_bridge_event
from agentmesh.models import Agent, EventKind, TaskState


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    db.init_db(tmp_path)
    return tmp_path


@pytest.fixture
def agent(data_dir: Path) -> Agent:
    a = Agent(agent_id="ewp_agent", cwd="/tmp")
    db.register_agent(a, data_dir)
    return a


# -- T1: Bridge event carries EWP identity envelope --

def test_bridge_receipt_carries_ewp_envelope(data_dir: Path) -> None:
    """ASSAY_RECEIPT payload contains _ewp_version, _ewp_task_id, _ewp_origin."""
    with patch("agentmesh.assay_bridge.shutil.which", return_value=None):
        emit_bridge_event(
            task_id="task_ewp_test",
            terminal_state="MERGED",
            agent_id="agent_1",
            data_dir=data_dir,
        )

    evts = events.read_events(data_dir)
    receipts = [e for e in evts if e.kind == EventKind.ASSAY_RECEIPT]
    assert len(receipts) == 1

    payload = receipts[0].payload
    assert payload["_ewp_version"] == "0"
    assert payload["_ewp_task_id"] == "task_ewp_test"
    assert payload["_ewp_origin"] == "agentmesh/assay_bridge"
    assert payload["_ewp_agent_id"] == "agent_1"


def test_bridge_receipt_carries_episode_id(data_dir: Path) -> None:
    """_ewp_episode_id present when episode_id is provided."""
    with patch("agentmesh.assay_bridge.shutil.which", return_value=None):
        emit_bridge_event(
            task_id="task_ep",
            terminal_state="MERGED",
            episode_id="ep_abc123",
            agent_id="agent_1",
            data_dir=data_dir,
        )

    evts = events.read_events(data_dir)
    receipts = [e for e in evts if e.kind == EventKind.ASSAY_RECEIPT]
    payload = receipts[0].payload
    assert payload["_ewp_episode_id"] == "ep_abc123"


def test_bridge_receipt_omits_episode_when_empty(data_dir: Path) -> None:
    """_ewp_episode_id absent when episode_id is empty string."""
    with patch("agentmesh.assay_bridge.shutil.which", return_value=None):
        emit_bridge_event(
            task_id="task_no_ep",
            terminal_state="ABORTED",
            episode_id="",
            data_dir=data_dir,
        )

    evts = events.read_events(data_dir)
    receipts = [e for e in evts if e.kind == EventKind.ASSAY_RECEIPT]
    payload = receipts[0].payload
    assert "_ewp_episode_id" not in payload


# -- T2: Orchestrator propagates episode_id through bridge --

def test_complete_task_propagates_episode_id(data_dir: Path, agent: Agent) -> None:
    """complete_task passes task.episode_id to bridge."""
    with patch("agentmesh.assay_bridge.shutil.which", return_value=None):
        task = orchestrator.create_task(
            "EWP test", episode_id="ep_orch_test", data_dir=data_dir,
        )
        orchestrator.assign_task(task.task_id, agent.agent_id, data_dir=data_dir)
        orchestrator.transition_task(task.task_id, TaskState.RUNNING, data_dir=data_dir)
        orchestrator.transition_task(task.task_id, TaskState.PR_OPEN, data_dir=data_dir)
        orchestrator.transition_task(task.task_id, TaskState.CI_PASS, data_dir=data_dir)
        orchestrator.transition_task(task.task_id, TaskState.REVIEW_PASS, data_dir=data_dir)
        orchestrator.complete_task(task.task_id, agent_id=agent.agent_id, data_dir=data_dir)

    evts = events.read_events(data_dir)
    receipts = [e for e in evts if e.kind == EventKind.ASSAY_RECEIPT]
    assert len(receipts) == 1
    assert receipts[0].payload["_ewp_episode_id"] == "ep_orch_test"
    assert receipts[0].payload["_ewp_task_id"] == task.task_id


def test_abort_task_propagates_episode_id(data_dir: Path, agent: Agent) -> None:
    """abort_task passes task.episode_id to bridge."""
    with patch("agentmesh.assay_bridge.shutil.which", return_value=None):
        task = orchestrator.create_task(
            "EWP abort", episode_id="ep_abort_test", data_dir=data_dir,
        )
        orchestrator.assign_task(task.task_id, agent.agent_id, data_dir=data_dir)
        orchestrator.abort_task(task.task_id, reason="test", data_dir=data_dir)

    evts = events.read_events(data_dir)
    receipts = [e for e in evts if e.kind == EventKind.ASSAY_RECEIPT]
    assert len(receipts) == 1
    assert receipts[0].payload["_ewp_episode_id"] == "ep_abort_test"


# -- T7: Unknown _ewp_ fields are preserved --

def test_unknown_ewp_fields_preserved_in_events(data_dir: Path) -> None:
    """Injecting extra _ewp_ fields into event payload does not cause errors."""
    payload = {
        "task_id": "task_future",
        "_ewp_version": "0",
        "_ewp_future_field": "test_value",
        "_ewp_another": 42,
    }
    events.append_event(
        kind=EventKind.ASSAY_RECEIPT,
        agent_id="test",
        payload=payload,
        data_dir=data_dir,
    )

    evts = events.read_events(data_dir)
    receipts = [e for e in evts if e.kind == EventKind.ASSAY_RECEIPT]
    assert len(receipts) == 1
    assert receipts[0].payload["_ewp_future_field"] == "test_value"
    assert receipts[0].payload["_ewp_another"] == 42


# -- T8: Absent _ewp_ fields don't break existing code --

def test_existing_bridge_tests_pass_with_ewp(data_dir: Path, agent: Agent) -> None:
    """Full lifecycle still works with EWP fields present (regression guard)."""
    with patch("agentmesh.assay_bridge.shutil.which", return_value=None):
        task = orchestrator.create_task("Regression guard", data_dir=data_dir)
        orchestrator.assign_task(task.task_id, agent.agent_id, data_dir=data_dir)
        orchestrator.transition_task(task.task_id, TaskState.RUNNING, data_dir=data_dir)
        orchestrator.transition_task(task.task_id, TaskState.PR_OPEN, data_dir=data_dir)
        orchestrator.transition_task(task.task_id, TaskState.CI_PASS, data_dir=data_dir)
        orchestrator.transition_task(task.task_id, TaskState.REVIEW_PASS, data_dir=data_dir)
        orchestrator.complete_task(task.task_id, agent_id=agent.agent_id, data_dir=data_dir)

    evts = events.read_events(data_dir)
    receipts = [e for e in evts if e.kind == EventKind.ASSAY_RECEIPT]
    assert len(receipts) == 1

    # EWP fields present
    p = receipts[0].payload
    assert p["_ewp_version"] == "0"
    assert p["_ewp_task_id"] == task.task_id
    assert p["_ewp_origin"] == "agentmesh/assay_bridge"

    # Original fields still intact
    assert p["task_id"] == task.task_id
    assert p["terminal_state"] == "MERGED"
    assert p["bridge_status"] == "BRIDGE_EMIT_DEGRADED"
