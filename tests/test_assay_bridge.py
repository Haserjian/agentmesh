"""Tests for the Assay bridge (ASSAY_RECEIPT event emission)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from agentmesh import db, events, orchestrator
from agentmesh.assay_bridge import BridgeResult, emit_bridge_event
from agentmesh.models import Agent, EventKind, TaskState


@pytest.fixture
def data_dir(tmp_path):
    """Fresh DB in a temp dir."""
    db.init_db(tmp_path)
    return tmp_path


@pytest.fixture
def agent(data_dir):
    a = Agent(agent_id="agent_bridge", cwd="/tmp")
    db.register_agent(a, data_dir)
    return a


@pytest.fixture
def repo_dir(tmp_path):
    """Create a fake repo directory."""
    d = tmp_path / "repo"
    d.mkdir()
    return d


def _make_completed_process(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["assay", "gate", "check"],
        returncode=returncode,
        stdout=stdout,
        stderr="",
    )


PASS_REPORT = {
    "command": "assay gate",
    "result": "PASS",
    "current_score": 42.5,
    "current_grade": "F",
    "baseline_score": None,
    "min_score": 0.0,
    "regression_detected": False,
    "reasons": [],
    "timestamp": "2026-02-24T00:00:00+00:00",
}

FAIL_REPORT = {
    "command": "assay gate",
    "result": "FAIL",
    "current_score": 10.0,
    "current_grade": "F",
    "baseline_score": 50.0,
    "min_score": 0.0,
    "regression_detected": True,
    "reasons": ["score dropped"],
    "timestamp": "2026-02-24T00:00:00+00:00",
}


# -- 1. OK: assay PASS --

def test_bridge_ok_assay_pass(data_dir, repo_dir):
    with patch("agentmesh.assay_bridge.shutil.which", return_value="/usr/bin/assay"), \
         patch("agentmesh.assay_bridge.subprocess.run") as mock_run:
        mock_run.return_value = _make_completed_process(json.dumps(PASS_REPORT), 0)

        result = emit_bridge_event(
            task_id="task_abc",
            terminal_state="MERGED",
            repo_path=repo_dir,
            agent_id="agent_1",
            data_dir=data_dir,
        )

    assert result.status == "BRIDGE_EMIT_OK"
    assert result.gate_report["result"] == "PASS"
    assert result.reason == ""


# -- 2. OK: assay FAIL (valid gate result) --

def test_bridge_ok_assay_fail(data_dir, repo_dir):
    with patch("agentmesh.assay_bridge.shutil.which", return_value="/usr/bin/assay"), \
         patch("agentmesh.assay_bridge.subprocess.run") as mock_run:
        mock_run.return_value = _make_completed_process(json.dumps(FAIL_REPORT), 1)

        result = emit_bridge_event(
            task_id="task_def",
            terminal_state="MERGED",
            repo_path=repo_dir,
            agent_id="agent_1",
            data_dir=data_dir,
        )

    assert result.status == "BRIDGE_EMIT_OK"
    assert result.gate_report["result"] == "FAIL"
    assert result.reason == ""


# -- 3. DEGRADED: no assay CLI --

def test_bridge_degraded_no_assay(data_dir, repo_dir):
    with patch("agentmesh.assay_bridge.shutil.which", return_value=None):
        result = emit_bridge_event(
            task_id="task_ghi",
            terminal_state="ABORTED",
            repo_path=repo_dir,
            agent_id="agent_1",
            data_dir=data_dir,
        )

    assert result.status == "BRIDGE_EMIT_DEGRADED"
    assert "not found" in result.reason


# -- 4. DEGRADED: timeout --

def test_bridge_degraded_timeout(data_dir, repo_dir):
    with patch("agentmesh.assay_bridge.shutil.which", return_value="/usr/bin/assay"), \
         patch("agentmesh.assay_bridge.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="assay", timeout=30)

        result = emit_bridge_event(
            task_id="task_jkl",
            terminal_state="MERGED",
            repo_path=repo_dir,
            agent_id="agent_1",
            data_dir=data_dir,
        )

    assert result.status == "BRIDGE_EMIT_DEGRADED"
    assert "timed out" in result.reason


# -- 5. DEGRADED: bad JSON output --

def test_bridge_degraded_bad_json(data_dir, repo_dir):
    with patch("agentmesh.assay_bridge.shutil.which", return_value="/usr/bin/assay"), \
         patch("agentmesh.assay_bridge.subprocess.run") as mock_run:
        mock_run.return_value = _make_completed_process("not json at all", 0)

        result = emit_bridge_event(
            task_id="task_mno",
            terminal_state="MERGED",
            repo_path=repo_dir,
            agent_id="agent_1",
            data_dir=data_dir,
        )

    assert result.status == "BRIDGE_EMIT_DEGRADED"
    assert "non-JSON" in result.reason


# -- 6. DEGRADED: exit code 3 (bad input) --

def test_bridge_degraded_exit_3(data_dir, repo_dir):
    with patch("agentmesh.assay_bridge.shutil.which", return_value="/usr/bin/assay"), \
         patch("agentmesh.assay_bridge.subprocess.run") as mock_run:
        mock_run.return_value = _make_completed_process("", 3)

        result = emit_bridge_event(
            task_id="task_pqr",
            terminal_state="MERGED",
            repo_path=repo_dir,
            agent_id="agent_1",
            data_dir=data_dir,
        )

    assert result.status == "BRIDGE_EMIT_DEGRADED"
    assert "bad input" in result.reason


# -- 7. DEGRADED: no repo_path, no spawns, and CWD is not a git repo --

def test_bridge_degraded_no_repo_path(data_dir, tmp_path):
    # Ensure CWD fallback doesn't find a .git dir
    non_git = tmp_path / "not_a_repo"
    non_git.mkdir()
    with patch("agentmesh.assay_bridge.Path.cwd", return_value=non_git):
        result = emit_bridge_event(
            task_id="task_stu",
            terminal_state="ABORTED",
            repo_path=None,
            agent_id="agent_1",
            data_dir=data_dir,
        )

    assert result.status == "BRIDGE_EMIT_DEGRADED"
    assert "no repo path" in result.reason


# -- 7b. CWD fallback: no spawns but CWD is a git repo --

def test_bridge_cwd_fallback_finds_git_repo(data_dir, tmp_path):
    """When no spawn records exist, fall back to CWD if it's a git repo."""
    fake_repo = tmp_path / "git_repo"
    fake_repo.mkdir()
    (fake_repo / ".git").mkdir()

    with patch("agentmesh.assay_bridge.Path.cwd", return_value=fake_repo), \
         patch("agentmesh.assay_bridge.shutil.which", return_value="/usr/bin/assay"), \
         patch("agentmesh.assay_bridge.subprocess.run") as mock_run:
        mock_run.return_value = _make_completed_process(json.dumps(PASS_REPORT), 0)

        result = emit_bridge_event(
            task_id="task_cwd_fallback",
            terminal_state="MERGED",
            repo_path=None,
            agent_id="agent_1",
            data_dir=data_dir,
        )

    assert result.status == "BRIDGE_EMIT_OK"
    assert result.gate_report["result"] == "PASS"
    # Verify assay was called with the CWD path
    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert str(fake_repo) in call_args


# -- 8. Event always emitted on OK --

def test_bridge_always_emits_event_ok(data_dir, repo_dir):
    with patch("agentmesh.assay_bridge.shutil.which", return_value="/usr/bin/assay"), \
         patch("agentmesh.assay_bridge.subprocess.run") as mock_run:
        mock_run.return_value = _make_completed_process(json.dumps(PASS_REPORT), 0)

        emit_bridge_event(
            task_id="task_evt_ok",
            terminal_state="MERGED",
            repo_path=repo_dir,
            agent_id="agent_1",
            data_dir=data_dir,
        )

    evts = events.read_events(data_dir)
    receipts = [e for e in evts if e.kind == EventKind.ASSAY_RECEIPT]
    assert len(receipts) == 1
    assert receipts[0].payload["bridge_status"] == "BRIDGE_EMIT_OK"
    assert receipts[0].payload["task_id"] == "task_evt_ok"
    assert "degraded_reason" not in receipts[0].payload


# -- 9. Event always emitted on DEGRADED --

def test_bridge_always_emits_event_degraded(data_dir):
    emit_bridge_event(
        task_id="task_evt_deg",
        terminal_state="ABORTED",
        repo_path=None,
        agent_id="agent_1",
        data_dir=data_dir,
    )

    evts = events.read_events(data_dir)
    receipts = [e for e in evts if e.kind == EventKind.ASSAY_RECEIPT]
    assert len(receipts) == 1
    assert receipts[0].payload["bridge_status"] == "BRIDGE_EMIT_DEGRADED"
    assert receipts[0].payload["degraded_reason"]


# -- 10. Integration: complete_task emits bridge event --

def test_complete_task_emits_bridge(data_dir, agent):
    with patch("agentmesh.assay_bridge.shutil.which", return_value=None):
        task = orchestrator.create_task("Bridge merge", data_dir=data_dir)
        orchestrator.assign_task(task.task_id, agent.agent_id, data_dir=data_dir)
        orchestrator.transition_task(task.task_id, TaskState.RUNNING, data_dir=data_dir)
        orchestrator.transition_task(task.task_id, TaskState.PR_OPEN, data_dir=data_dir)
        orchestrator.transition_task(task.task_id, TaskState.CI_PASS, data_dir=data_dir)
        orchestrator.transition_task(task.task_id, TaskState.REVIEW_PASS, data_dir=data_dir)
        orchestrator.complete_task(task.task_id, agent_id=agent.agent_id, data_dir=data_dir)

    evts = events.read_events(data_dir)
    receipts = [e for e in evts if e.kind == EventKind.ASSAY_RECEIPT]
    assert len(receipts) == 1
    assert receipts[0].payload["terminal_state"] == "MERGED"
    assert receipts[0].payload["task_id"] == task.task_id


# -- 11. Integration: abort_task emits bridge event --

def test_abort_task_emits_bridge(data_dir, agent):
    with patch("agentmesh.assay_bridge.shutil.which", return_value=None):
        task = orchestrator.create_task("Bridge abort", data_dir=data_dir)
        orchestrator.abort_task(task.task_id, reason="test", agent_id=agent.agent_id, data_dir=data_dir)

    evts = events.read_events(data_dir)
    receipts = [e for e in evts if e.kind == EventKind.ASSAY_RECEIPT]
    assert len(receipts) == 1
    assert receipts[0].payload["terminal_state"] == "ABORTED"
    assert receipts[0].payload["task_id"] == task.task_id


# -- 12. Invariant: no silent path -- every terminal transition emits exactly one receipt --

@pytest.mark.parametrize("terminal", ["MERGED", "ABORTED"])
def test_no_silent_path(data_dir, agent, terminal):
    """Every terminal transition must emit exactly one ASSAY_RECEIPT, never zero."""
    with patch("agentmesh.assay_bridge.shutil.which", return_value=None):
        task = orchestrator.create_task(f"Silent-{terminal}", data_dir=data_dir)
        orchestrator.assign_task(task.task_id, agent.agent_id, data_dir=data_dir)
        orchestrator.transition_task(task.task_id, TaskState.RUNNING, data_dir=data_dir)
        orchestrator.transition_task(task.task_id, TaskState.PR_OPEN, data_dir=data_dir)
        orchestrator.transition_task(task.task_id, TaskState.CI_PASS, data_dir=data_dir)
        orchestrator.transition_task(task.task_id, TaskState.REVIEW_PASS, data_dir=data_dir)

        if terminal == "MERGED":
            orchestrator.complete_task(task.task_id, agent_id=agent.agent_id, data_dir=data_dir)
        else:
            orchestrator.abort_task(task.task_id, reason="test", agent_id=agent.agent_id, data_dir=data_dir)

    evts = events.read_events(data_dir)
    receipts = [
        e for e in evts
        if e.kind == EventKind.ASSAY_RECEIPT and e.payload["task_id"] == task.task_id
    ]
    assert len(receipts) == 1, (
        f"Expected exactly 1 ASSAY_RECEIPT for {terminal}, got {len(receipts)}"
    )
    assert receipts[0].payload["bridge_status"] in ("BRIDGE_EMIT_OK", "BRIDGE_EMIT_DEGRADED")
