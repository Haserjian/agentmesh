"""Tests for Alpha Gate report generation."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agentmesh import db, events, orchestrator
from agentmesh.alpha_gate import (
    build_alpha_gate_report,
    sanitize_alpha_gate_report,
)
from agentmesh.cli import app
from agentmesh.models import Agent, EventKind, TaskState, _now

runner = CliRunner()


def test_alpha_gate_report_passes_happy_path(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db.init_db(data_dir)
    db.register_agent(Agent(agent_id="alpha_agent", cwd="/tmp"), data_dir)

    task = orchestrator.create_task("alpha", data_dir=data_dir)
    orchestrator.assign_task(task.task_id, "alpha_agent", branch="feat/alpha", data_dir=data_dir)
    orchestrator.transition_task(task.task_id, TaskState.RUNNING, data_dir=data_dir)
    orchestrator.transition_task(task.task_id, TaskState.PR_OPEN, data_dir=data_dir)
    orchestrator.transition_task(task.task_id, TaskState.CI_PASS, data_dir=data_dir)
    orchestrator.transition_task(task.task_id, TaskState.REVIEW_PASS, data_dir=data_dir)
    orchestrator.complete_task(task.task_id, agent_id="alpha_agent", data_dir=data_dir)

    db.create_spawn(
        spawn_id="spawn_alpha",
        task_id=task.task_id,
        attempt_id="att_alpha",
        agent_id="alpha_agent",
        pid=123,
        worktree_path="/tmp/wt",
        branch="feat/alpha",
        episode_id="",
        context_hash="sha256:abc",
        started_at=_now(),
        data_dir=data_dir,
    )
    db.update_spawn("spawn_alpha", ended_at=_now(), outcome="success", data_dir=data_dir)

    events.append_event(
        EventKind.GC,
        payload={"watchdog": "scan", "harvested_spawns": ["spawn_alpha"]},
        data_dir=data_dir,
    )

    report = build_alpha_gate_report(data_dir=data_dir, ci_log_text="... VERIFIED ...")
    assert report["overall_pass"] is True
    assert report["checks"]["merged_task_count"]["pass"] is True
    assert report["checks"]["witness_verified_ci"]["pass"] is True
    assert report["checks"]["full_transition_receipts"]["pass"] is True
    assert report["checks"]["watchdog_handled_event"]["pass"] is True
    assert report["checks"]["no_orphan_finalization_loss"]["pass"] is True


def test_alpha_gate_report_fails_without_witness_when_required(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db.init_db(data_dir)
    report = build_alpha_gate_report(data_dir=data_dir, ci_log_text="", require_witness_verified=True)
    assert report["overall_pass"] is False
    assert report["checks"]["witness_verified_ci"]["pass"] is False


def test_alpha_gate_prefers_structured_ci_result(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db.init_db(data_dir)
    report = build_alpha_gate_report(
        data_dir=data_dir,
        ci_log_text="NO VERIFIED TOKEN HERE",
        ci_result={"witness": {"status": "VERIFIED"}},
        require_witness_verified=True,
    )
    assert report["checks"]["witness_verified_ci"]["pass"] is True
    assert report["checks"]["witness_verified_ci"]["source"] == "ci_result"


def test_sanitize_alpha_gate_report_redacts_id_lists() -> None:
    raw = {
        "overall_pass": False,
        "checks": {
            "full_transition_receipts": {
                "pass": False,
                "missing_tasks": ["task_a", "task_b"],
                "state_mismatch_tasks": ["task_c"],
            },
            "no_orphan_finalization_loss": {
                "pass": False,
                "bad_spawns": ["spawn_1"],
            },
            "merged_task_count": {"pass": True, "actual": 2, "expected_min": 1},
        },
        "summary": {"tasks_total": 3, "events_total": 12, "spawns_total": 1},
    }
    clean = sanitize_alpha_gate_report(raw)
    assert clean["sanitized"] is True
    ft = clean["checks"]["full_transition_receipts"]
    assert "missing_tasks" not in ft
    assert ft["missing_tasks_count"] == 2
    assert ft["state_mismatch_tasks_count"] == 1
    nol = clean["checks"]["no_orphan_finalization_loss"]
    assert "bad_spawns" not in nol
    assert nol["bad_spawns_count"] == 1


def test_cli_sanitize_alpha_gate_report(tmp_path: Path, monkeypatch) -> None:
    in_path = tmp_path / "raw.json"
    out_path = tmp_path / "public.json"
    in_path.write_text(
        json.dumps(
            {
                "overall_pass": True,
                "checks": {
                    "full_transition_receipts": {
                        "pass": True,
                        "missing_tasks": ["task_secret"],
                        "state_mismatch_tasks": [],
                    }
                },
                "summary": {"tasks_total": 1, "events_total": 1, "spawns_total": 1},
            }
        )
    )

    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        [
            "sanitize-alpha-gate-report",
            "--in",
            str(in_path),
            "--out",
            str(out_path),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    saved = json.loads(out_path.read_text())
    assert saved["sanitized"] is True
    assert "missing_tasks" not in saved["checks"]["full_transition_receipts"]
