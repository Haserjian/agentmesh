from __future__ import annotations

from agentmesh.evidence_kpi import (
    compute_coverage,
    evaluate_required_checks,
    select_latest_check_runs,
)


def test_select_latest_check_runs_prefers_highest_id() -> None:
    runs = [
        {"id": 101, "name": "lineage", "status": "completed", "conclusion": "failure"},
        {"id": 102, "name": "lineage", "status": "completed", "conclusion": "success"},
        {"id": 201, "name": "assay-gate", "status": "completed", "conclusion": "success"},
    ]
    latest = select_latest_check_runs(runs)
    assert latest["lineage"]["id"] == 102
    assert latest["lineage"]["conclusion"] == "success"


def test_evaluate_required_checks_complete_success() -> None:
    latest = {
        "lineage": {"id": 1, "status": "completed", "conclusion": "success"},
        "assay-gate": {"id": 2, "status": "completed", "conclusion": "success"},
    }
    complete, statuses, missing, failed = evaluate_required_checks(
        latest, ["lineage", "assay-gate"]
    )
    assert complete is True
    assert statuses == {"lineage": "success", "assay-gate": "success"}
    assert missing == []
    assert failed == []


def test_evaluate_required_checks_missing_and_failed() -> None:
    latest = {
        "lineage": {"id": 1, "status": "completed", "conclusion": "failure"},
    }
    complete, statuses, missing, failed = evaluate_required_checks(
        latest, ["lineage", "assay-gate"]
    )
    assert complete is False
    assert statuses["lineage"] == "failure"
    assert statuses["assay-gate"] == "missing"
    assert missing == ["assay-gate"]
    assert failed == ["lineage"]


def test_compute_coverage_handles_zero_total() -> None:
    assert compute_coverage(0, 0) == 0.0
    assert compute_coverage(3, 4) == 75.0
