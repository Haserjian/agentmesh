from __future__ import annotations

from datetime import timezone

from agentmesh.evidence_kpi import (
    DEFAULT_REQUIRED_CHECKS,
    compute_coverage,
    evaluate_required_checks,
    normalize_window_days,
    parse_date_or_datetime_utc,
    select_latest_check_runs,
)


def test_select_latest_check_runs_prefers_highest_id() -> None:
    runs = [
        {"id": 101, "name": "lineage", "status": "completed", "conclusion": "failure"},
        {"id": 102, "name": "lineage", "status": "completed", "conclusion": "success"},
        {"id": 201, "name": "assay-gate", "status": "completed", "conclusion": "success"},
        {"id": 301, "name": "assay-verify", "status": "completed", "conclusion": "success"},
    ]
    latest = select_latest_check_runs(runs)
    assert latest["lineage"]["id"] == 102
    assert latest["lineage"]["conclusion"] == "success"


def test_evaluate_required_checks_complete_success() -> None:
    latest = {
        "lineage": {"id": 1, "status": "completed", "conclusion": "success"},
        "assay-gate": {"id": 2, "status": "completed", "conclusion": "success"},
        "assay-verify": {"id": 3, "status": "completed", "conclusion": "success"},
    }
    complete, statuses, missing, failed = evaluate_required_checks(
        latest, ["lineage", "assay-gate", "assay-verify"]
    )
    assert complete is True
    assert statuses == {"lineage": "success", "assay-gate": "success", "assay-verify": "success"}
    assert missing == []
    assert failed == []


def test_evaluate_required_checks_missing_and_failed() -> None:
    latest = {
        "lineage": {"id": 1, "status": "completed", "conclusion": "failure"},
        "assay-gate": {"id": 2, "status": "completed", "conclusion": "success"},
    }
    complete, statuses, missing, failed = evaluate_required_checks(
        latest, ["lineage", "assay-gate", "assay-verify"]
    )
    assert complete is False
    assert statuses["lineage"] == "failure"
    assert statuses["assay-gate"] == "success"
    assert statuses["assay-verify"] == "missing"
    assert missing == ["assay-verify"]
    assert failed == ["lineage"]


def test_compute_coverage_handles_zero_total() -> None:
    assert compute_coverage(0, 0) == 0.0
    assert compute_coverage(3, 4) == 75.0


def test_default_required_checks_include_assay_verify() -> None:
    assert DEFAULT_REQUIRED_CHECKS == ("lineage", "assay-gate", "assay-verify")


def test_normalize_window_days_includes_primary_and_defaults() -> None:
    windows = normalize_window_days(30, [14, 30], include_default_slices=True)
    assert windows == [7, 14, 30]


def test_parse_date_or_datetime_utc_accepts_date_and_iso() -> None:
    dt_date = parse_date_or_datetime_utc("2026-02-24")
    assert dt_date.tzinfo == timezone.utc
    assert dt_date.isoformat() == "2026-02-24T00:00:00+00:00"

    dt_iso = parse_date_or_datetime_utc("2026-02-24T12:34:56Z")
    assert dt_iso.tzinfo == timezone.utc
    assert dt_iso.isoformat() == "2026-02-24T12:34:56+00:00"
