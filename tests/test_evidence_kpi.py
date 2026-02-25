from __future__ import annotations

from datetime import timezone

from agentmesh.evidence_kpi import (
    DEFAULT_REQUIRED_CHECKS,
    _summarize_check_pass_rates,
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


def test_summarize_check_pass_rates_aggregates_per_check() -> None:
    prs = [
        {
            "check_statuses": {
                "lineage": "success",
                "assay-gate": "success",
                "assay-verify": "missing",
            }
        },
        {
            "check_statuses": {
                "lineage": "failure",
                "assay-gate": "success",
                "assay-verify": "incomplete:queued",
            }
        },
        {
            "check_statuses": {
                "lineage": "success",
                "assay-gate": "failure",
                "assay-verify": "success",
            }
        },
    ]
    rates = _summarize_check_pass_rates(prs, ["lineage", "assay-gate", "assay-verify"])

    assert rates["lineage"]["passing_prs"] == 2
    assert rates["lineage"]["ai_prs_total"] == 3
    assert rates["lineage"]["coverage_pct"] == 66.67
    assert rates["lineage"]["missing_prs"] == 0
    assert rates["lineage"]["failed_prs"] == 1
    assert rates["lineage"]["incomplete_prs"] == 0

    assert rates["assay-gate"]["passing_prs"] == 2
    assert rates["assay-gate"]["coverage_pct"] == 66.67
    assert rates["assay-gate"]["failed_prs"] == 1

    assert rates["assay-verify"]["passing_prs"] == 1
    assert rates["assay-verify"]["coverage_pct"] == 33.33
    assert rates["assay-verify"]["missing_prs"] == 1
    assert rates["assay-verify"]["failed_prs"] == 1
    assert rates["assay-verify"]["incomplete_prs"] == 1


def test_summarize_check_pass_rates_handles_missing_status_dict() -> None:
    rates = _summarize_check_pass_rates([{}, {"check_statuses": None}], ["lineage"])
    assert rates["lineage"]["ai_prs_total"] == 2
    assert rates["lineage"]["passing_prs"] == 0
    assert rates["lineage"]["missing_prs"] == 2
    assert rates["lineage"]["coverage_pct"] == 0.0
