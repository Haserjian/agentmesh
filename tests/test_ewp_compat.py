"""CCOI envelope protocol tests (supersedes EWP v0 compat tests).

Validates that the CCOI envelope (CCOI_V0_1.md §4.1) is:
  - Present in ASSAY_RECEIPT payloads on OK path (T1)
  - Present in ASSAY_RECEIPT payloads on DEGRADED path (T1b)
  - Carries episode_id when available (T2)
  - Has exact required field set (T3)
  - Uses stable task-scoped correlation (T4)
  - Coexists with degradation metadata (T5)

Migrated from EWP v0 (_ewp_* fields) to CCOI v0.1 envelope.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agentmesh import db, events, orchestrator
from agentmesh.assay_bridge import (
    CCOI_ENVELOPE_REQUIRED_FIELDS,
    emit_bridge_event,
)
from agentmesh.models import Agent, EventKind, TaskState


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    db.init_db(tmp_path)
    return tmp_path


@pytest.fixture
def agent(data_dir: Path) -> Agent:
    a = Agent(agent_id="ccoi_agent", cwd="/tmp")
    db.register_agent(a, data_dir)
    return a


@pytest.fixture
def repo_dir(tmp_path: Path) -> Path:
    d = tmp_path / "repo"
    d.mkdir()
    return d


# -- T1: Envelope present on OK path --

def test_ok_receipt_carries_ccoi_envelope(data_dir: Path, repo_dir: Path) -> None:
    """ASSAY_RECEIPT payload contains ccoi_envelope on OK."""
    import json
    import subprocess

    with patch("agentmesh.assay_bridge.shutil.which", return_value="/usr/bin/assay"), \
         patch("agentmesh.assay_bridge.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["assay"], returncode=0,
            stdout=json.dumps({"result": "PASS", "current_score": 42.5}),
            stderr="",
        )
        emit_bridge_event(
            task_id="task_ccoi_ok",
            terminal_state="MERGED",
            repo_path=repo_dir,
            agent_id="agent_1",
            data_dir=data_dir,
        )

    evts = events.read_events(data_dir)
    receipts = [e for e in evts if e.kind == EventKind.ASSAY_RECEIPT]
    assert len(receipts) == 1

    payload = receipts[0].payload
    assert "ccoi_envelope" in payload
    env = payload["ccoi_envelope"]
    assert env["source_organ"] == "agentmesh"
    assert env["target_organ"] == "assay-toolkit"
    assert env["authority_class"] == "AUDITING"
    assert env["primitive"] == "QUERY"


# -- T1b: Envelope present on DEGRADED path --

def test_degraded_receipt_carries_ccoi_envelope(data_dir: Path) -> None:
    """ASSAY_RECEIPT payload contains ccoi_envelope even on DEGRADED."""
    with patch("agentmesh.assay_bridge.shutil.which", return_value=None):
        emit_bridge_event(
            task_id="task_ccoi_deg",
            terminal_state="ABORTED",
            agent_id="agent_1",
            data_dir=data_dir,
        )

    evts = events.read_events(data_dir)
    receipts = [e for e in evts if e.kind == EventKind.ASSAY_RECEIPT]
    assert len(receipts) == 1

    payload = receipts[0].payload
    assert "ccoi_envelope" in payload
    env = payload["ccoi_envelope"]
    assert env["source_organ"] == "agentmesh"
    assert env["authority_class"] == "AUDITING"


# -- T2: Episode ID propagation --

def test_envelope_carries_episode_id(data_dir: Path) -> None:
    """episode_id appears in envelope payload when provided."""
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
    env = receipts[0].payload["ccoi_envelope"]
    assert env["payload"]["episode_id"] == "ep_abc123"


def test_envelope_omits_episode_when_empty(data_dir: Path) -> None:
    """episode_id absent from envelope payload when empty string."""
    with patch("agentmesh.assay_bridge.shutil.which", return_value=None):
        emit_bridge_event(
            task_id="task_no_ep",
            terminal_state="ABORTED",
            episode_id="",
            data_dir=data_dir,
        )

    evts = events.read_events(data_dir)
    receipts = [e for e in evts if e.kind == EventKind.ASSAY_RECEIPT]
    env = receipts[0].payload["ccoi_envelope"]
    assert "episode_id" not in env["payload"]


# -- T3: Exact protocol shape --

def test_envelope_has_exact_required_fields(data_dir: Path) -> None:
    """Envelope contains exactly the CCOI v0.1 §4.1 required fields."""
    with patch("agentmesh.assay_bridge.shutil.which", return_value=None):
        result = emit_bridge_event(
            task_id="task_shape",
            terminal_state="MERGED",
            data_dir=data_dir,
        )

    env = result.envelope
    assert env is not None
    assert set(env.keys()) == CCOI_ENVELOPE_REQUIRED_FIELDS


def test_envelope_version_is_0_1(data_dir: Path) -> None:
    """Protocol version matches CCOI v0.1."""
    with patch("agentmesh.assay_bridge.shutil.which", return_value=None):
        result = emit_bridge_event(
            task_id="task_ver",
            terminal_state="MERGED",
            data_dir=data_dir,
        )

    assert result.envelope["ccoi_version"] == "0.1"


# -- T4: Stable task-scoped correlation --

def test_correlation_id_is_task_id(data_dir: Path) -> None:
    """correlation_id uses the stable task identity, not a random UUID."""
    with patch("agentmesh.assay_bridge.shutil.which", return_value=None):
        result = emit_bridge_event(
            task_id="task_corr_stable",
            terminal_state="MERGED",
            data_dir=data_dir,
        )

    assert result.envelope["correlation_id"] == "task_corr_stable"


def test_correlation_stable_across_outcomes(data_dir: Path, repo_dir: Path) -> None:
    """Same task_id produces same correlation on OK and DEGRADED paths."""
    import json
    import subprocess

    # DEGRADED path
    with patch("agentmesh.assay_bridge.shutil.which", return_value=None):
        degraded = emit_bridge_event(
            task_id="task_same",
            terminal_state="ABORTED",
            data_dir=data_dir,
        )

    # OK path
    with patch("agentmesh.assay_bridge.shutil.which", return_value="/usr/bin/assay"), \
         patch("agentmesh.assay_bridge.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["assay"], returncode=0,
            stdout=json.dumps({"result": "PASS"}),
            stderr="",
        )
        ok = emit_bridge_event(
            task_id="task_same",
            terminal_state="MERGED",
            repo_path=repo_dir,
            data_dir=data_dir,
        )

    assert degraded.envelope["correlation_id"] == ok.envelope["correlation_id"]
    assert degraded.envelope["correlation_id"] == "task_same"


# -- T5: Envelope and degradation coexist --

def test_degraded_envelope_carries_degradation_in_payload(data_dir: Path) -> None:
    """On DEGRADED, envelope payload includes degraded_reason."""
    with patch("agentmesh.assay_bridge.shutil.which", return_value=None):
        result = emit_bridge_event(
            task_id="task_coexist",
            terminal_state="ABORTED",
            data_dir=data_dir,
        )

    assert result.envelope is not None
    assert result.reason != ""
    assert result.envelope["payload"]["degraded_reason"] == result.reason
    assert result.envelope["payload"]["bridge_status"] == "BRIDGE_EMIT_DEGRADED"


def test_ok_envelope_has_no_degradation(data_dir: Path, repo_dir: Path) -> None:
    """On OK, envelope payload does not carry degraded_reason."""
    import json
    import subprocess

    with patch("agentmesh.assay_bridge.shutil.which", return_value="/usr/bin/assay"), \
         patch("agentmesh.assay_bridge.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["assay"], returncode=0,
            stdout=json.dumps({"result": "PASS"}),
            stderr="",
        )
        result = emit_bridge_event(
            task_id="task_ok_no_deg",
            terminal_state="MERGED",
            repo_path=repo_dir,
            data_dir=data_dir,
        )

    assert result.envelope is not None
    assert "degraded_reason" not in result.envelope["payload"]
    assert result.envelope["payload"]["bridge_status"] == "BRIDGE_EMIT_OK"


# -- T6: Orchestrator propagates through CCOI envelope --

def test_complete_task_propagates_episode_in_envelope(
    data_dir: Path, agent: Agent,
) -> None:
    """complete_task passes episode_id into the CCOI envelope."""
    with patch("agentmesh.assay_bridge.shutil.which", return_value=None):
        task = orchestrator.create_task(
            "CCOI test", episode_id="ep_orch_ccoi", data_dir=data_dir,
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
    env = receipts[0].payload["ccoi_envelope"]
    assert env["payload"]["episode_id"] == "ep_orch_ccoi"
    assert env["correlation_id"] == task.task_id


def test_abort_task_propagates_episode_in_envelope(
    data_dir: Path, agent: Agent,
) -> None:
    """abort_task passes episode_id into the CCOI envelope."""
    with patch("agentmesh.assay_bridge.shutil.which", return_value=None):
        task = orchestrator.create_task(
            "CCOI abort", episode_id="ep_abort_ccoi", data_dir=data_dir,
        )
        orchestrator.assign_task(task.task_id, agent.agent_id, data_dir=data_dir)
        orchestrator.abort_task(task.task_id, reason="test", data_dir=data_dir)

    evts = events.read_events(data_dir)
    receipts = [e for e in evts if e.kind == EventKind.ASSAY_RECEIPT]
    assert len(receipts) == 1
    env = receipts[0].payload["ccoi_envelope"]
    assert env["payload"]["episode_id"] == "ep_abort_ccoi"


# -- T7: Full lifecycle regression guard --

def test_full_lifecycle_with_ccoi_envelope(
    data_dir: Path, agent: Agent,
) -> None:
    """Full lifecycle still works with CCOI envelope (regression guard)."""
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

    p = receipts[0].payload
    # CCOI envelope present
    assert "ccoi_envelope" in p
    env = p["ccoi_envelope"]
    assert env["ccoi_version"] == "0.1"
    assert env["source_organ"] == "agentmesh"
    assert env["correlation_id"] == task.task_id

    # Original fields still intact
    assert p["task_id"] == task.task_id
    assert p["terminal_state"] == "MERGED"
    assert p["bridge_status"] == "BRIDGE_EMIT_DEGRADED"
