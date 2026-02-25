from __future__ import annotations

from datetime import timezone

from agentmesh.evidence_kpi import (
    DEFAULT_REQUIRED_CHECKS,
    _summarize_check_attempts,
    _summarize_check_pass_rates,
    _summarize_reliability,
    build_trend_point,
    compute_coverage,
    evaluate_required_checks,
    merge_trend_history,
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


def test_summarize_check_attempts_flags_reruns() -> None:
    runs = [
        {"name": "lineage"},
        {"name": "lineage"},
        {"name": "assay-gate"},
        {"name": "assay-verify"},
        {"name": "other"},
    ]
    counts, rerun_checks = _summarize_check_attempts(runs, ["lineage", "assay-gate", "assay-verify"])
    assert counts == {"lineage": 2, "assay-gate": 1, "assay-verify": 1}
    assert rerun_checks == ["lineage"]


def test_summarize_reliability_aggregates_rerun_metrics() -> None:
    prs = [
        {"rerun_detected": True, "check_attempt_counts": {"lineage": 2, "assay-gate": 1, "assay-verify": 1}},
        {"rerun_detected": False, "check_attempt_counts": {"lineage": 1, "assay-gate": 1, "assay-verify": 1}},
        {"rerun_detected": True, "check_attempt_counts": {"lineage": 1, "assay-gate": 3, "assay-verify": 1}},
    ]
    summary = _summarize_reliability(prs, ["lineage", "assay-gate", "assay-verify"])
    assert summary["prs_with_reruns"] == 2
    assert summary["ai_prs_total"] == 3
    assert summary["rerun_rate_pct"] == 66.67
    assert summary["by_check"]["lineage"]["rerun_prs"] == 1
    assert summary["by_check"]["lineage"]["rerun_rate_pct"] == 33.33
    assert summary["by_check"]["assay-gate"]["rerun_prs"] == 1
    assert summary["by_check"]["assay-verify"]["rerun_prs"] == 0


def test_build_trend_point_includes_core_metrics() -> None:
    report = {
        "generated_at": "2026-02-25T00:00:00Z",
        "repo": "Haserjian/agentmesh",
        "base": "main",
        "window_days": 30,
        "coverage_pct": 40.0,
        "passing_prs": 6,
        "ai_prs_total": 15,
        "enforcement_date": "2026-02-25T04:40:16Z",
        "check_pass_rates": {"lineage": {"coverage_pct": 100.0}},
        "reliability": {"rerun_rate_pct": 10.0},
        "since_enforcement": {
            "coverage_pct": 100.0,
            "passing_prs": 6,
            "ai_prs_total": 6,
            "check_pass_rates": {"lineage": {"coverage_pct": 100.0}},
            "reliability": {"rerun_rate_pct": 0.0},
        },
    }
    point = build_trend_point(
        report,
        run_id="123",
        run_attempt="1",
        run_url="https://example/run/123",
        workflow="Evidence KPI",
        event_name="schedule",
        ref_name="main",
    )
    assert point["run_id"] == "123"
    assert point["coverage_pct"] == 40.0
    assert point["since_enforcement_coverage_pct"] == 100.0
    assert point["check_pass_rates"]["lineage"]["coverage_pct"] == 100.0
    assert point["reliability"]["rerun_rate_pct"] == 10.0
    assert point["since_enforcement_reliability"]["rerun_rate_pct"] == 0.0


def test_merge_trend_history_dedupes_and_caps() -> None:
    existing = [
        {"run_id": "100", "generated_at": "2026-02-24T00:00:00Z", "coverage_pct": 0.0},
        {"run_id": "101", "generated_at": "2026-02-25T00:00:00Z", "coverage_pct": 50.0},
        {"run_id": "101", "generated_at": "2026-02-25T00:00:00Z", "coverage_pct": 50.0},
    ]
    point = {"run_id": "102", "generated_at": "2026-02-26T00:00:00Z", "coverage_pct": 100.0}
    merged = merge_trend_history(existing, point, max_points=2)
    assert [row["run_id"] for row in merged] == ["101", "102"]
