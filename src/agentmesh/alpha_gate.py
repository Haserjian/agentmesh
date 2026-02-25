"""Alpha gate report utilities for first real orchestrated run validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import db, events, weaver
from .models import EventKind, TaskState


def _task_transition_coverage(tasks: list[Any], data_dir: Path | None) -> dict[str, Any]:
    rows = events.read_events(data_dir=data_dir)
    by_task: dict[str, list[dict[str, Any]]] = {}
    for evt in rows:
        if evt.kind != EventKind.TASK_TRANSITION:
            continue
        payload = evt.payload if isinstance(evt.payload, dict) else {}
        task_id = payload.get("task_id", "")
        if not task_id:
            continue
        by_task.setdefault(task_id, []).append(payload)

    missing: list[str] = []
    mismatch: list[str] = []
    for task in tasks:
        payloads = by_task.get(task.task_id, [])
        if not payloads:
            missing.append(task.task_id)
            continue
        final_to = payloads[-1].get("to_state", "")
        if final_to != task.state.value:
            mismatch.append(task.task_id)

    return {
        "pass": not missing and not mismatch,
        "missing_tasks": missing,
        "state_mismatch_tasks": mismatch,
    }


def _watchdog_handled(rows: list[Any]) -> bool:
    for evt in rows:
        if evt.kind != EventKind.GC:
            continue
        payload = evt.payload if isinstance(evt.payload, dict) else {}
        if payload.get("watchdog") != "scan":
            continue
        if (
            payload.get("stale_agents")
            or payload.get("aborted_tasks")
            or payload.get("harvested_spawns")
            or payload.get("timed_out_spawns")
            or payload.get("cost_exceeded_tasks")
        ):
            return True
    return False


def _spawn_loss_check(data_dir: Path | None) -> dict[str, Any]:
    bad: list[str] = []
    rows = db.list_spawns_db(active_only=False, data_dir=data_dir)
    for row in rows:
        ended = row.get("ended_at", "")
        outcome = row.get("outcome", "")
        if (not ended and outcome) or (ended and not outcome):
            bad.append(row.get("spawn_id", ""))
    return {"pass": not bad, "bad_spawns": bad}


def _witness_verified_from_result(ci_result: dict[str, Any] | None) -> bool | None:
    if not isinstance(ci_result, dict):
        return None

    # Direct booleans
    direct = ci_result.get("witness_verified")
    if isinstance(direct, bool):
        return direct

    # Flat status key
    status = ci_result.get("witness_status")
    if isinstance(status, str):
        return status.upper() == "VERIFIED"

    # Nested structures commonly used by action outputs/reports
    witness = ci_result.get("witness")
    if isinstance(witness, dict):
        status = witness.get("status")
        if isinstance(status, str):
            return status.upper() == "VERIFIED"
        verified = witness.get("verified")
        if isinstance(verified, bool):
            return verified
        verified_count = witness.get("verified_count")
        invalid_count = witness.get("invalid_count", 0)
        missing_count = witness.get("missing_count", 0)
        if isinstance(verified_count, int):
            if isinstance(invalid_count, int) and invalid_count > 0:
                return False
            if isinstance(missing_count, int) and missing_count > 0:
                return False
            return verified_count > 0

    checks = ci_result.get("checks")
    if isinstance(checks, dict):
        w = checks.get("witness_verified_ci")
        if isinstance(w, dict) and isinstance(w.get("pass"), bool):
            return w["pass"]

    return None


def build_alpha_gate_report(
    data_dir: Path | None,
    *,
    ci_log_text: str = "",
    ci_result: dict[str, Any] | None = None,
    require_witness_verified: bool = True,
) -> dict[str, Any]:
    tasks = db.list_tasks(data_dir=data_dir, limit=5000)
    rows = events.read_events(data_dir=data_dir)
    merged_count = sum(1 for t in tasks if t.state == TaskState.MERGED)
    transition_cov = _task_transition_coverage(tasks, data_dir)
    watchdog_ok = _watchdog_handled(rows)
    spawn_loss = _spawn_loss_check(data_dir)
    weave_ok, weave_err = weaver.verify_weave(data_dir=data_dir)

    witness_from_result = _witness_verified_from_result(ci_result)
    if witness_from_result is None:
        witness_verified = "VERIFIED" in ci_log_text if require_witness_verified else True
        witness_source = "ci_log_text"
    else:
        witness_verified = witness_from_result if require_witness_verified else True
        witness_source = "ci_result"
    checks = {
        "merged_task_count": {"pass": merged_count >= 1, "actual": merged_count, "expected_min": 1},
        "witness_verified_ci": {
            "pass": witness_verified,
            "required": require_witness_verified,
            "source": witness_source,
        },
        "weave_chain_intact": {"pass": weave_ok, "error": weave_err},
        "full_transition_receipts": transition_cov,
        "watchdog_handled_event": {"pass": watchdog_ok},
        "no_orphan_finalization_loss": spawn_loss,
    }

    overall = all(item.get("pass", False) for item in checks.values())
    return {
        "overall_pass": overall,
        "checks": checks,
        "summary": {
            "tasks_total": len(tasks),
            "events_total": len(rows),
            "spawns_total": len(db.list_spawns_db(active_only=False, data_dir=data_dir)),
        },
    }


def write_alpha_gate_report(
    out_path: Path,
    data_dir: Path | None,
    *,
    ci_log_text: str = "",
    ci_result: dict[str, Any] | None = None,
    require_witness_verified: bool = True,
) -> dict[str, Any]:
    report = build_alpha_gate_report(
        data_dir=data_dir,
        ci_log_text=ci_log_text,
        ci_result=ci_result,
        require_witness_verified=require_witness_verified,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n")
    return report


def sanitize_alpha_gate_report(report: dict[str, Any]) -> dict[str, Any]:
    """Produce a public-safe summary from a potentially sensitive raw report."""
    checks = report.get("checks", {}) if isinstance(report, dict) else {}
    if not isinstance(checks, dict):
        checks = {}

    out_checks: dict[str, Any] = {}
    for name, value in checks.items():
        row = value if isinstance(value, dict) else {}
        out_row: dict[str, Any] = {"pass": bool(row.get("pass", False))}

        # Keep only safe quantitative fields.
        for key in ("actual", "expected_min", "required", "source"):
            if key in row:
                out_row[key] = row[key]

        # Replace sensitive id lists with counts.
        if "missing_tasks" in row:
            missing = row.get("missing_tasks", [])
            out_row["missing_tasks_count"] = len(missing) if isinstance(missing, list) else 0
        if "state_mismatch_tasks" in row:
            mismatch = row.get("state_mismatch_tasks", [])
            out_row["state_mismatch_tasks_count"] = len(mismatch) if isinstance(mismatch, list) else 0
        if "bad_spawns" in row:
            bad = row.get("bad_spawns", [])
            out_row["bad_spawns_count"] = len(bad) if isinstance(bad, list) else 0

        out_checks[name] = out_row

    summary = report.get("summary", {}) if isinstance(report, dict) else {}
    if not isinstance(summary, dict):
        summary = {}

    out_summary = {}
    for key in ("tasks_total", "events_total", "spawns_total"):
        value = summary.get(key, 0)
        out_summary[key] = int(value) if isinstance(value, int) else 0

    return {
        "overall_pass": bool(report.get("overall_pass", False)) if isinstance(report, dict) else False,
        "checks": out_checks,
        "summary": out_summary,
        "sanitized": True,
    }


def write_sanitized_alpha_gate_report(
    in_path: Path,
    out_path: Path,
) -> dict[str, Any]:
    raw = json.loads(in_path.read_text())
    if not isinstance(raw, dict):
        raise ValueError("raw report must be a JSON object")
    clean = sanitize_alpha_gate_report(raw)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(clean, indent=2) + "\n")
    return clean
