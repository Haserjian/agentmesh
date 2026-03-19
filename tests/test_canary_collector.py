"""Tests for canary artifact collector — fail-closed semantics and trust enum."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Import the collector module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import collect_canary_artifacts as collector


# -- ci_run fail-closed tests --


def test_ci_run_empty_checks_is_not_passed():
    """Empty check list must not produce all_required_passed=True."""
    with patch.object(collector, "_run") as mock_run:
        mock_run.return_value = type("P", (), {"returncode": 0, "stdout": "[]", "stderr": ""})()
        result = collector.collect_ci_run(99)

    assert result["all_required_passed"] is False
    assert len(result["missing_required"]) == len(collector.REQUIRED_CHECKS)


def test_ci_run_partial_checks_is_not_passed():
    """If only some required checks are present, all_required_passed must be False."""
    partial = json.dumps([
        {"name": "lineage", "state": "SUCCESS", "bucket": "pass"},
        # assay-gate and assay-verify missing
    ])
    with patch.object(collector, "_run") as mock_run:
        mock_run.return_value = type("P", (), {"returncode": 0, "stdout": partial, "stderr": ""})()
        result = collector.collect_ci_run(99)

    assert result["all_required_passed"] is False
    assert "assay-gate" in result["missing_required"]
    assert "assay-verify" in result["missing_required"]


def test_ci_run_all_required_present_and_passing():
    """All required checks present and passing -> True."""
    full = json.dumps([
        {"name": "lineage", "state": "SUCCESS", "bucket": "pass"},
        {"name": "assay-gate", "state": "SUCCESS", "bucket": "pass"},
        {"name": "assay-verify", "state": "SUCCESS", "bucket": "pass"},
        {"name": "dco", "state": "SUCCESS", "bucket": "pass"},
    ])
    with patch.object(collector, "_run") as mock_run:
        mock_run.return_value = type("P", (), {"returncode": 0, "stdout": full, "stderr": ""})()
        result = collector.collect_ci_run(99)

    assert result["all_required_passed"] is True
    assert result["missing_required"] == []


def test_ci_run_required_check_failed():
    """A required check that failed -> all_required_passed=False."""
    data = json.dumps([
        {"name": "lineage", "state": "SUCCESS", "bucket": "pass"},
        {"name": "assay-gate", "state": "FAILURE", "bucket": "fail"},
        {"name": "assay-verify", "state": "SUCCESS", "bucket": "pass"},
    ])
    with patch.object(collector, "_run") as mock_run:
        mock_run.return_value = type("P", (), {"returncode": 0, "stdout": data, "stderr": ""})()
        result = collector.collect_ci_run(99)

    assert result["all_required_passed"] is False


def test_ci_run_gh_failure_is_not_passed():
    """gh pr checks returning error -> all_required_passed=False."""
    with patch.object(collector, "_run") as mock_run:
        mock_run.return_value = type("P", (), {"returncode": 1, "stdout": "", "stderr": "not found"})()
        result = collector.collect_ci_run(99)

    assert result["all_required_passed"] is False
    assert "error" in result


def test_ci_run_json_parse_failure():
    """Non-JSON stdout -> all_required_passed=False."""
    with patch.object(collector, "_run") as mock_run:
        mock_run.return_value = type("P", (), {"returncode": 1, "stdout": "not json", "stderr": ""})()
        result = collector.collect_ci_run(99)

    assert result["all_required_passed"] is False


# -- trust_evaluation enum tests --


def test_trust_enum_accept_from_structured_artifact(tmp_path):
    """Tier 1: structured artifact with ACCEPT -> trust_decision=ACCEPT."""
    artifact = {
        "schema_version": "1",
        "trust_decision": "ACCEPT",
        "fingerprint_verified": True,
        "source": "ci_structured_artifact",
    }

    def mock_try_structured(run_id):
        return dict(artifact)

    checks = json.dumps([
        {"name": "assay-verify", "state": "SUCCESS", "bucket": "pass",
         "link": "https://github.com/x/y/actions/runs/111/job/222"},
    ])
    with patch.object(collector, "_run") as mock_run, \
         patch.object(collector, "_try_structured_artifact", side_effect=mock_try_structured):
        mock_run.return_value = type("P", (), {"returncode": 0, "stdout": checks, "stderr": ""})()
        result = collector.collect_trust_evaluation(99)

    assert result["trust_decision"] == "ACCEPT"
    assert result["source"] == "ci_structured_artifact"


def test_trust_enum_inconclusive_when_check_missing():
    """Missing assay-verify check -> INCONCLUSIVE, not FAIL or ACCEPT."""
    checks = json.dumps([
        {"name": "lineage", "state": "SUCCESS", "bucket": "pass", "link": ""},
    ])
    with patch.object(collector, "_run") as mock_run:
        mock_run.return_value = type("P", (), {"returncode": 0, "stdout": checks, "stderr": ""})()
        result = collector.collect_trust_evaluation(99)

    assert result["trust_decision"] == "INCONCLUSIVE"


def test_trust_enum_inconclusive_when_check_failed():
    """assay-verify check failed -> INCONCLUSIVE (not REJECT, because we can't distinguish
    policy rejection from infra failure without reading the artifact)."""
    checks = json.dumps([
        {"name": "assay-verify", "state": "FAILURE", "bucket": "fail",
         "link": "https://github.com/x/y/actions/runs/111/job/222"},
    ])
    with patch.object(collector, "_run") as mock_run:
        mock_run.return_value = type("P", (), {"returncode": 0, "stdout": checks, "stderr": ""})()
        result = collector.collect_trust_evaluation(99)

    assert result["trust_decision"] == "INCONCLUSIVE"
    assert "infra failure" in result["reason"]


def test_trust_falls_back_to_log_scrape():
    """Tier 2: no structured artifact, but logs contain decision -> use it."""
    checks = json.dumps([
        {"name": "assay-verify", "state": "SUCCESS", "bucket": "pass",
         "link": "https://github.com/x/y/actions/runs/111/job/222"},
    ])

    log_output = (
        "2026-03-19T20:54:24Z Fingerprint verified: a211e933a2512129...\n"
        "2026-03-19T20:54:25Z Trust decision: accept\n"
        "2026-03-19T20:54:25Z   signers.yaml hash=e306732d3ee3f5aa\n"
    )

    call_count = [0]
    def mock_run(cmd, **kwargs):
        call_count[0] += 1
        if "pr" in cmd and "checks" in cmd:
            return type("P", (), {"returncode": 0, "stdout": checks, "stderr": ""})()
        if "logs" in str(cmd):
            return type("P", (), {"returncode": 0, "stdout": log_output, "stderr": ""})()
        return type("P", (), {"returncode": 1, "stdout": "", "stderr": ""})()

    with patch.object(collector, "_run", side_effect=mock_run), \
         patch.object(collector, "_try_structured_artifact", return_value=None):
        result = collector.collect_trust_evaluation(99)

    assert result["trust_decision"] == "ACCEPT"
    assert result["source"] == "ci_job_logs"
    assert result["fingerprint_verified"] is True


def test_trust_inconclusive_when_no_source():
    """Tier 3: no artifact, no useful logs -> INCONCLUSIVE."""
    checks = json.dumps([
        {"name": "assay-verify", "state": "SUCCESS", "bucket": "pass",
         "link": "https://github.com/x/y/actions/runs/111/job/222"},
    ])

    with patch.object(collector, "_run") as mock_run, \
         patch.object(collector, "_try_structured_artifact", return_value=None), \
         patch.object(collector, "_try_log_scrape", return_value=None):
        mock_run.return_value = type("P", (), {"returncode": 0, "stdout": checks, "stderr": ""})()
        result = collector.collect_trust_evaluation(99)

    assert result["trust_decision"] == "INCONCLUSIVE"
    assert "could not extract" in result["reason"]
