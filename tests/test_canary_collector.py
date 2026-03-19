"""Tests for canary artifact collector — fail-closed semantics and trust enum."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Import the collector module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import collect_canary_artifacts as collector


def _proc(returncode=0, stdout="", stderr=""):
    """Helper to create a mock subprocess result."""
    return type("P", (), {"returncode": returncode, "stdout": stdout, "stderr": stderr})()


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

    def mock_try_structured(run_id, *, expected_sha="", expected_pr=0):
        return dict(artifact), {"attempted": True, "status": "ok"}

    checks = json.dumps([
        {"name": "assay-verify", "state": "SUCCESS", "bucket": "pass",
         "link": "https://github.com/x/y/actions/runs/111/job/222"},
    ])
    with patch.object(collector, "_run") as mock_run, \
         patch.object(collector, "_try_structured_artifact", side_effect=mock_try_structured):
        mock_run.return_value = _proc(stdout=checks)
        result = collector.collect_trust_evaluation(99)

    assert result["trust_decision"] == "ACCEPT"
    assert result["evidence_tier"] == "structured_ci_artifact"


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

    def mock_run(cmd, **kwargs):
        if "pr" in cmd and "checks" in cmd:
            return _proc(stdout=checks)
        if "logs" in str(cmd):
            return _proc(stdout=log_output)
        return _proc(returncode=1)

    with patch.object(collector, "_run", side_effect=mock_run), \
         patch.object(collector, "_try_structured_artifact",
                      return_value=(None, {"attempted": True, "status": "artifact_missing"})):
        result = collector.collect_trust_evaluation(99)

    assert result["trust_decision"] == "ACCEPT"
    assert result["evidence_tier"] == "ci_job_logs"


def test_trust_inconclusive_when_no_source():
    """Tier 3: no artifact, no useful logs -> INCONCLUSIVE."""
    checks = json.dumps([
        {"name": "assay-verify", "state": "SUCCESS", "bucket": "pass",
         "link": "https://github.com/x/y/actions/runs/111/job/222"},
    ])

    with patch.object(collector, "_run") as mock_run, \
         patch.object(collector, "_try_structured_artifact",
                      return_value=(None, {"attempted": True, "status": "artifact_missing"})), \
         patch.object(collector, "_try_log_scrape",
                      return_value=(None, {"attempted": True, "status": "parse_error", "parser": "none"})):
        mock_run.return_value = _proc(stdout=checks)
        result = collector.collect_trust_evaluation(99)

    assert result["trust_decision"] == "INCONCLUSIVE"
    assert "could not extract" in result["reason"]


# ============================================================================
# Provenance validation tests (Task 1)
# ============================================================================


def test_structured_artifact_rejected_on_sha_mismatch(tmp_path):
    """Tier 1 artifact with wrong commit_sha must be rejected -> fall to tier 2."""
    artifact = {
        "schema_version": "1",
        "trust_decision": "ACCEPT",
        "commit_sha": "wrong_sha_from_different_run",
        "workflow_run_id": "111",
    }

    def mock_try(run_id, *, expected_sha="", expected_pr=0):
        # Real implementation validates provenance; mismatch -> (None, attempt)
        prov = {"expected_sha": expected_sha, "observed_sha": artifact["commit_sha"], "match": False}
        if expected_sha and artifact["commit_sha"] != expected_sha:
            return None, {"attempted": True, "status": "provenance_mismatch", "provenance": prov}
        return dict(artifact), {"attempted": True, "status": "ok", "provenance": prov}

    checks = json.dumps([
        {"name": "assay-verify", "state": "SUCCESS", "bucket": "pass",
         "link": "https://github.com/x/y/actions/runs/111/job/222"},
    ])
    with patch.object(collector, "_run") as mock_run, \
         patch.object(collector, "_try_structured_artifact", side_effect=mock_try), \
         patch.object(collector, "_try_log_scrape",
                      return_value=(None, {"attempted": True, "status": "parse_error", "parser": "none"})):
        mock_run.return_value = _proc(stdout=checks)
        result = collector.collect_trust_evaluation(99, head_sha="correct_sha_abc123")

    # Should NOT be ACCEPT from the mismatched artifact
    assert result["trust_decision"] == "INCONCLUSIVE"
    assert result["structured_artifact"]["status"] == "provenance_mismatch"


def test_structured_artifact_rejected_on_run_id_mismatch():
    """Tier 1 artifact from different workflow run must be rejected."""
    def mock_try(run_id, *, expected_sha="", expected_pr=0):
        return None, {"attempted": True, "status": "provenance_mismatch",
                       "provenance": {"expected_run_id": run_id, "observed_run_id": "999", "match": False}}

    checks = json.dumps([
        {"name": "assay-verify", "state": "SUCCESS", "bucket": "pass",
         "link": "https://github.com/x/y/actions/runs/111/job/222"},
    ])
    with patch.object(collector, "_run") as mock_run, \
         patch.object(collector, "_try_structured_artifact", side_effect=mock_try), \
         patch.object(collector, "_try_log_scrape",
                      return_value=(None, {"attempted": True, "status": "parse_error", "parser": "none"})):
        mock_run.return_value = _proc(stdout=checks)
        result = collector.collect_trust_evaluation(99, head_sha="abc123")

    assert result["trust_decision"] == "INCONCLUSIVE"


def test_structured_artifact_accepted_when_provenance_matches():
    """Tier 1 artifact with matching provenance -> ACCEPT as tier 1."""
    artifact = {
        "schema_version": "1",
        "trust_decision": "ACCEPT",
        "commit_sha": "abc123",
        "workflow_run_id": "111",
    }

    def mock_try(run_id, *, expected_sha="", expected_pr=0):
        prov = {"expected_sha": expected_sha, "observed_sha": "abc123",
                "expected_run_id": run_id, "observed_run_id": "111", "match": True}
        return dict(artifact, source="ci_structured_artifact"), {"attempted": True, "status": "ok", "provenance": prov}

    checks = json.dumps([
        {"name": "assay-verify", "state": "SUCCESS", "bucket": "pass",
         "link": "https://github.com/x/y/actions/runs/111/job/222"},
    ])
    with patch.object(collector, "_run") as mock_run, \
         patch.object(collector, "_try_structured_artifact", side_effect=mock_try):
        mock_run.return_value = _proc(stdout=checks)
        result = collector.collect_trust_evaluation(99, head_sha="abc123")

    assert result["trust_decision"] == "ACCEPT"
    assert result["evidence_tier"] == "structured_ci_artifact"


def test_structured_artifact_missing_provenance_still_accepted_but_downgraded():
    """Artifact without provenance fields is accepted but provenance.match is None."""
    artifact = {
        "schema_version": "1",
        "trust_decision": "ACCEPT",
        # No commit_sha or workflow_run_id -> can't validate provenance
    }

    def mock_try(run_id, *, expected_sha="", expected_pr=0):
        prov = {"expected_sha": expected_sha, "observed_sha": "",
                "expected_run_id": run_id, "observed_run_id": "", "match": None}
        return dict(artifact, source="ci_structured_artifact"), {"attempted": True, "status": "ok", "provenance": prov}

    checks = json.dumps([
        {"name": "assay-verify", "state": "SUCCESS", "bucket": "pass",
         "link": "https://github.com/x/y/actions/runs/111/job/222"},
    ])
    with patch.object(collector, "_run") as mock_run, \
         patch.object(collector, "_try_structured_artifact", side_effect=mock_try):
        mock_run.return_value = _proc(stdout=checks)
        result = collector.collect_trust_evaluation(99, head_sha="abc123")

    assert result["trust_decision"] == "ACCEPT"
    assert result["evidence_tier"] == "structured_ci_artifact"
    assert result["provenance"]["match"] is None


# ============================================================================
# Axis separation tests (Task 2) — schema v2 shape
# ============================================================================


def test_schema_v2_has_evidence_tier_field():
    """trust_evaluation output must include evidence_tier as independent axis."""
    checks = json.dumps([
        {"name": "assay-verify", "state": "SUCCESS", "bucket": "pass",
         "link": "https://github.com/x/y/actions/runs/111/job/222"},
    ])
    with patch.object(collector, "_run") as mock_run, \
         patch.object(collector, "_try_structured_artifact",
                      return_value=(None, {"attempted": True, "status": "artifact_missing"})), \
         patch.object(collector, "_try_log_scrape",
                      return_value=(None, {"attempted": True, "status": "parse_error", "parser": "none"})):
        mock_run.return_value = _proc(stdout=checks)
        result = collector.collect_trust_evaluation(99)

    assert "evidence_tier" in result
    assert result["evidence_tier"] == "none"


def test_schema_v2_has_collection_status():
    """trust_evaluation output must include collection_status."""
    checks = json.dumps([
        {"name": "assay-verify", "state": "SUCCESS", "bucket": "pass",
         "link": "https://github.com/x/y/actions/runs/111/job/222"},
    ])
    with patch.object(collector, "_run") as mock_run, \
         patch.object(collector, "_try_structured_artifact",
                      return_value=(None, {"attempted": True, "status": "artifact_missing"})), \
         patch.object(collector, "_try_log_scrape",
                      return_value=(None, {"attempted": True, "status": "parse_error", "parser": "none"})):
        mock_run.return_value = _proc(stdout=checks)
        result = collector.collect_trust_evaluation(99)

    assert "collection_status" in result
    assert result["collection_status"] in ("ok", "unavailable", "parse_error", "api_error")


def test_schema_v2_structured_artifact_has_nested_subrecord():
    """Tier 1 result must include structured_artifact subrecord."""
    artifact = {
        "schema_version": "1",
        "trust_decision": "ACCEPT",
        "commit_sha": "abc123",
        "workflow_run_id": "111",
    }

    def mock_try(run_id, *, expected_sha="", expected_pr=0):
        return dict(artifact, source="ci_structured_artifact"), {"attempted": True, "status": "ok"}

    checks = json.dumps([
        {"name": "assay-verify", "state": "SUCCESS", "bucket": "pass",
         "link": "https://github.com/x/y/actions/runs/111/job/222"},
    ])
    with patch.object(collector, "_run") as mock_run, \
         patch.object(collector, "_try_structured_artifact", side_effect=mock_try):
        mock_run.return_value = _proc(stdout=checks)
        result = collector.collect_trust_evaluation(99, head_sha="abc123")

    assert "structured_artifact" in result
    assert result["structured_artifact"]["attempted"] is True
    assert result["structured_artifact"]["status"] == "ok"


def test_schema_v2_log_fallback_has_nested_subrecord():
    """Tier 2 result must include log_fallback subrecord."""
    log_output = "2026-03-19T20:54:25Z Trust decision: accept\n"

    def mock_run(cmd, **kwargs):
        if "pr" in cmd and "checks" in cmd:
            checks = json.dumps([
                {"name": "assay-verify", "state": "SUCCESS", "bucket": "pass",
                 "link": "https://github.com/x/y/actions/runs/111/job/222"},
            ])
            return _proc(stdout=checks)
        if "logs" in str(cmd):
            return _proc(stdout=log_output)
        return _proc(returncode=1)

    with patch.object(collector, "_run", side_effect=mock_run), \
         patch.object(collector, "_try_structured_artifact",
                      return_value=(None, {"attempted": True, "status": "artifact_missing"})):
        result = collector.collect_trust_evaluation(99)

    assert result["evidence_tier"] == "ci_job_logs"
    assert "log_fallback" in result
    assert result["log_fallback"]["attempted"] is True
    assert result["log_fallback"]["status"] == "ok"


def test_schema_v2_provenance_block_present():
    """Provenance comparison must be in output when structured artifact attempted."""
    artifact = {
        "schema_version": "1",
        "trust_decision": "ACCEPT",
        "commit_sha": "abc123",
        "workflow_run_id": "111",
    }

    def mock_try(run_id, *, expected_sha="", expected_pr=0):
        prov = {"expected_sha": expected_sha, "observed_sha": "abc123",
                "expected_run_id": run_id, "observed_run_id": "111", "match": True}
        return dict(artifact, source="ci_structured_artifact"), {"attempted": True, "status": "ok", "provenance": prov}

    checks = json.dumps([
        {"name": "assay-verify", "state": "SUCCESS", "bucket": "pass",
         "link": "https://github.com/x/y/actions/runs/111/job/222"},
    ])
    with patch.object(collector, "_run") as mock_run, \
         patch.object(collector, "_try_structured_artifact", side_effect=mock_try):
        mock_run.return_value = _proc(stdout=checks)
        result = collector.collect_trust_evaluation(99, head_sha="abc123")

    assert "provenance" in result
    assert result["provenance"]["expected_sha"] == "abc123"
    assert result["provenance"]["match"] is True


# ============================================================================
# Sentinel parsing tests (Task 3)
# ============================================================================


def test_log_scrape_parses_sentinel_v1():
    """Sentinel TRUST_RESULT_V1 line should be parsed by log scraper."""
    sentinel_payload = json.dumps({"v": 1, "d": "ACCEPT", "fp": True, "sid": "ci-assay-signer"})
    log_output = (
        "2026-03-19T20:54:24Z Fingerprint verified: a211e933...\n"
        "2026-03-19T20:54:25Z Trust decision: accept\n"
        f"2026-03-19T20:54:25Z TRUST_RESULT_V1 {sentinel_payload}\n"
    )

    def mock_run(cmd, **kwargs):
        if "logs" in str(cmd):
            return _proc(stdout=log_output)
        return _proc(returncode=1)

    with patch.object(collector, "_run", side_effect=mock_run):
        data, attempt = collector._try_log_scrape("222")

    assert data is not None
    assert data["trust_decision"] == "ACCEPT"
    assert attempt["parser"] == "sentinel_v1"


def test_log_scrape_sentinel_preferred_over_prose():
    """If sentinel and prose disagree, sentinel wins."""
    sentinel_payload = json.dumps({"v": 1, "d": "REJECT", "fp": True, "sid": "ci-assay-signer"})
    log_output = (
        "2026-03-19T20:54:25Z Trust decision: accept\n"  # prose says accept
        f"2026-03-19T20:54:25Z TRUST_RESULT_V1 {sentinel_payload}\n"  # sentinel says REJECT
    )

    def mock_run(cmd, **kwargs):
        if "logs" in str(cmd):
            return _proc(stdout=log_output)
        return _proc(returncode=1)

    with patch.object(collector, "_run", side_effect=mock_run):
        data, attempt = collector._try_log_scrape("222")

    assert data is not None
    assert data["trust_decision"] == "REJECT"
    assert attempt["parser"] == "sentinel_v1"


def test_log_scrape_malformed_sentinel_falls_to_prose():
    """Malformed sentinel JSON should not crash; fall back to prose parsing."""
    log_output = (
        "2026-03-19T20:54:25Z TRUST_RESULT_V1 {not valid json\n"
        "2026-03-19T20:54:25Z Trust decision: accept\n"
    )

    def mock_run(cmd, **kwargs):
        if "logs" in str(cmd):
            return _proc(stdout=log_output)
        return _proc(returncode=1)

    with patch.object(collector, "_run", side_effect=mock_run):
        data, attempt = collector._try_log_scrape("222")

    assert data is not None
    assert data["trust_decision"] == "ACCEPT"
    assert attempt["parser"] == "prose_v1"
