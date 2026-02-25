"""Canary lane: single-task evidence-complete lifecycle test.

Proves the minimal happy path from PLANNED -> MERGED with full evidence
chain validation. Complements test_parallel_demo.py (which tests concurrency)
and test_orchestrator.py (which tests the state machine mechanics).

This test validates:
  1. Full state machine traversal: all 7 states visited in order
  2. ASSAY_RECEIPT payload structure on terminal transition
  3. Attempt lifecycle: created on assign, ended on complete
  4. Weave event integrity across all transitions
  5. Alpha gate report structure and per-check detail
  6. Sanitized gate report (public-safe) correctness
  7. Event log hash chain integrity
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agentmesh import db, events, orchestrator, weaver
from agentmesh.alpha_gate import build_alpha_gate_report, sanitize_alpha_gate_report
from agentmesh.models import Agent, EventKind, TaskState


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Fresh DB in a temp dir."""
    db.init_db(tmp_path)
    return tmp_path


@pytest.fixture
def agent(data_dir: Path) -> Agent:
    """Register a single canary agent."""
    a = Agent(agent_id="canary_agent", cwd="/tmp")
    db.register_agent(a, data_dir)
    return a


def test_canary_lane_full_lifecycle(data_dir: Path, agent: Agent) -> None:
    """Single task traverses all 7 states with full evidence chain."""

    with patch("agentmesh.assay_bridge.shutil.which", return_value=None):
        # -- PLANNED --
        task = orchestrator.create_task(
            "Canary lane task",
            description="Single-task evidence-complete lifecycle",
            data_dir=data_dir,
        )
        assert task.state == TaskState.PLANNED

        # -- ASSIGNED --
        task = orchestrator.assign_task(
            task.task_id, agent.agent_id, branch="feat/canary", data_dir=data_dir,
        )
        assert task.state == TaskState.ASSIGNED
        assert task.assigned_agent_id == agent.agent_id

        # Attempt created
        attempts = db.list_attempts(task.task_id, data_dir)
        assert len(attempts) == 1
        assert attempts[0].agent_id == agent.agent_id
        assert not attempts[0].outcome  # not yet finished (empty string)

        # -- RUNNING --
        task = orchestrator.transition_task(
            task.task_id, TaskState.RUNNING, agent_id=agent.agent_id, data_dir=data_dir,
        )
        assert task.state == TaskState.RUNNING

        # -- PR_OPEN --
        task = orchestrator.transition_task(
            task.task_id, TaskState.PR_OPEN, agent_id=agent.agent_id,
            data_dir=data_dir, pr_url="https://github.com/test/repo/pull/1",
        )
        assert task.state == TaskState.PR_OPEN

        # -- CI_PASS --
        task = orchestrator.transition_task(
            task.task_id, TaskState.CI_PASS, agent_id=agent.agent_id, data_dir=data_dir,
        )
        assert task.state == TaskState.CI_PASS

        # -- REVIEW_PASS --
        task = orchestrator.transition_task(
            task.task_id, TaskState.REVIEW_PASS, agent_id=agent.agent_id, data_dir=data_dir,
        )
        assert task.state == TaskState.REVIEW_PASS

        # -- MERGED (terminal) --
        task = orchestrator.complete_task(
            task.task_id, agent_id=agent.agent_id, data_dir=data_dir,
        )
        assert task.state == TaskState.MERGED

    # ===================================================================
    # 1. ASSAY_RECEIPT payload structure
    # ===================================================================
    all_events = events.read_events(data_dir)
    receipts = [e for e in all_events if e.kind == EventKind.ASSAY_RECEIPT]
    assert len(receipts) == 1, f"Expected 1 receipt, got {len(receipts)}"

    receipt = receipts[0]
    payload = receipt.payload
    assert payload["task_id"] == task.task_id
    assert payload["terminal_state"] == "MERGED"
    assert payload["bridge_status"] == "BRIDGE_EMIT_DEGRADED"
    assert "degraded_reason" in payload

    # ===================================================================
    # 2. Attempt lifecycle
    # ===================================================================
    attempts = db.list_attempts(task.task_id, data_dir)
    assert len(attempts) == 1
    final_attempt = attempts[0]
    assert final_attempt.outcome == "success"
    assert final_attempt.ended_at is not None

    # ===================================================================
    # 3. TASK_TRANSITION events cover all 7 states
    # ===================================================================
    transitions = [
        e for e in all_events
        if e.kind == EventKind.TASK_TRANSITION
        and e.payload.get("task_id") == task.task_id
    ]
    to_states = [t.payload["to_state"] for t in transitions]
    expected_states = ["planned", "assigned", "running", "pr_open", "ci_pass", "review_pass", "merged"]
    assert to_states == expected_states, (
        f"State sequence mismatch: got {to_states}, expected {expected_states}"
    )

    # ===================================================================
    # 4. Weave event integrity
    # ===================================================================
    weave_ok, weave_err = weaver.verify_weave(data_dir=data_dir)
    assert weave_ok, f"Weave verification failed: {weave_err}"

    weave_events_list = db.list_weave_events(data_dir)
    sequences = [e.sequence_id for e in weave_events_list]
    assert sequences == list(range(1, len(sequences) + 1)), (
        f"Weave sequence not monotonic: {sequences}"
    )

    # ===================================================================
    # 5. Event log hash chain integrity
    # ===================================================================
    chain_ok, chain_err = events.verify_chain(data_dir)
    assert chain_ok, f"Event chain integrity failed: {chain_err}"

    # ===================================================================
    # 6. Alpha gate report structure
    # ===================================================================
    gate = build_alpha_gate_report(data_dir, require_witness_verified=False)

    assert gate["checks"]["merged_task_count"]["pass"] is True
    assert gate["checks"]["merged_task_count"]["actual"] == 1

    assert gate["checks"]["weave_chain_intact"]["pass"] is True
    assert gate["checks"]["weave_chain_intact"]["error"] == ""

    assert gate["checks"]["full_transition_receipts"]["pass"] is True
    assert gate["checks"]["full_transition_receipts"]["missing_tasks"] == []
    assert gate["checks"]["full_transition_receipts"]["state_mismatch_tasks"] == []

    assert gate["checks"]["no_orphan_finalization_loss"]["pass"] is True
    assert gate["checks"]["no_orphan_finalization_loss"]["bad_spawns"] == []

    assert gate["summary"]["tasks_total"] == 1
    assert gate["summary"]["events_total"] > 0

    # ===================================================================
    # 7. Sanitized gate report (public-safe)
    # ===================================================================
    sanitized = sanitize_alpha_gate_report(gate)
    assert sanitized["sanitized"] is True
    assert sanitized["checks"]["merged_task_count"]["pass"] is True
    assert sanitized["checks"]["merged_task_count"]["actual"] == 1
    # Sensitive ID lists replaced by counts
    assert "missing_tasks" not in sanitized["checks"]["full_transition_receipts"]
    assert "missing_tasks_count" in sanitized["checks"]["full_transition_receipts"]
    assert sanitized["checks"]["full_transition_receipts"]["missing_tasks_count"] == 0


def test_canary_lane_abort_path(data_dir: Path, agent: Agent) -> None:
    """Abort path emits ASSAY_RECEIPT with terminal_state=ABORTED."""

    with patch("agentmesh.assay_bridge.shutil.which", return_value=None):
        task = orchestrator.create_task("Will abort", data_dir=data_dir)
        orchestrator.assign_task(task.task_id, agent.agent_id, data_dir=data_dir)
        orchestrator.transition_task(task.task_id, TaskState.RUNNING, data_dir=data_dir)
        task = orchestrator.abort_task(task.task_id, reason="canary abort test", data_dir=data_dir)

    assert task.state == TaskState.ABORTED

    all_events = events.read_events(data_dir)
    receipts = [
        e for e in all_events
        if e.kind == EventKind.ASSAY_RECEIPT
        and e.payload.get("task_id") == task.task_id
    ]
    assert len(receipts) == 1
    assert receipts[0].payload["terminal_state"] == "ABORTED"
    assert receipts[0].payload["bridge_status"] == "BRIDGE_EMIT_DEGRADED"


def test_canary_lane_no_duplicate_receipt_on_retry(data_dir: Path, agent: Agent) -> None:
    """Re-creating and completing a second task doesn't duplicate receipts."""

    with patch("agentmesh.assay_bridge.shutil.which", return_value=None):
        # First task: full lifecycle
        t1 = orchestrator.create_task("Task 1", data_dir=data_dir)
        orchestrator.assign_task(t1.task_id, agent.agent_id, data_dir=data_dir)
        orchestrator.transition_task(t1.task_id, TaskState.RUNNING, data_dir=data_dir)
        orchestrator.transition_task(t1.task_id, TaskState.PR_OPEN, data_dir=data_dir)
        orchestrator.transition_task(t1.task_id, TaskState.CI_PASS, data_dir=data_dir)
        orchestrator.transition_task(t1.task_id, TaskState.REVIEW_PASS, data_dir=data_dir)
        orchestrator.complete_task(t1.task_id, agent_id=agent.agent_id, data_dir=data_dir)

        # Second task: full lifecycle
        t2 = orchestrator.create_task("Task 2", data_dir=data_dir)
        orchestrator.assign_task(t2.task_id, agent.agent_id, data_dir=data_dir)
        orchestrator.transition_task(t2.task_id, TaskState.RUNNING, data_dir=data_dir)
        orchestrator.transition_task(t2.task_id, TaskState.PR_OPEN, data_dir=data_dir)
        orchestrator.transition_task(t2.task_id, TaskState.CI_PASS, data_dir=data_dir)
        orchestrator.transition_task(t2.task_id, TaskState.REVIEW_PASS, data_dir=data_dir)
        orchestrator.complete_task(t2.task_id, agent_id=agent.agent_id, data_dir=data_dir)

    all_events = events.read_events(data_dir)
    receipts = [e for e in all_events if e.kind == EventKind.ASSAY_RECEIPT]

    # Exactly 2 receipts: one per task, no duplicates
    assert len(receipts) == 2
    receipt_task_ids = {r.payload["task_id"] for r in receipts}
    assert receipt_task_ids == {t1.task_id, t2.task_id}
