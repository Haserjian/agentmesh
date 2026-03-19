#!/usr/bin/env python3
"""Collect canary artifact bundle for a proof-carrying PR.

Usage:
    # Pre-merge (after CI passes):
    python scripts/collect_canary_artifacts.py --pr 42 --phase pre

    # Post-merge:
    python scripts/collect_canary_artifacts.py --pr 42 --phase post

    # Both phases at once (if PR is already merged):
    python scripts/collect_canary_artifacts.py --pr 42 --phase full

Artifacts are written to artifacts/canary/<episode_id>/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "1"
REQUIRED_CHECKS = {"lineage", "assay-gate", "assay-verify"}


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def _run_json(cmd: list[str], **kwargs) -> dict:
    proc = _run(cmd, **kwargs)
    if proc.returncode != 0:
        return {"_error": proc.stderr.strip(), "_returncode": proc.returncode}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"_raw": proc.stdout.strip(), "_returncode": proc.returncode}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def get_pr_info(pr: int) -> dict:
    """Fetch PR metadata from GitHub."""
    fields = "number,title,state,mergedAt,mergeCommit,headRefName,headRefOid,url"
    proc = _run(["gh", "pr", "view", str(pr), "--json", fields])
    if proc.returncode != 0:
        print(f"Error fetching PR #{pr}: {proc.stderr}", file=sys.stderr)
        sys.exit(1)
    return json.loads(proc.stdout)


def get_episode_from_commit(sha: str) -> str:
    """Extract episode ID from commit trailers."""
    proc = _run(["git", "log", "-1", "--format=%(trailers:key=AgentMesh-Episode,valueonly)", sha])
    return proc.stdout.strip()


def collect_episode(episode_id: str, pr_info: dict) -> dict:
    """Artifact 1: episode.json"""
    return {
        "schema_version": SCHEMA_VERSION,
        "episode_id": episode_id,
        "pr_number": pr_info["number"],
        "pr_url": pr_info["url"],
        "branch": pr_info["headRefName"],
        "head_sha": pr_info["headRefOid"],
        "collected_at": _now(),
    }


def collect_lineage(episode_id: str, pr_info: dict) -> dict:
    """Artifact 2: lineage.json"""
    sha = pr_info["headRefOid"]
    proc = _run(["git", "log", "-1", "--format=%(trailers:key=AgentMesh-Episode,valueonly)", sha])
    trailer_value = proc.stdout.strip()
    return {
        "schema_version": SCHEMA_VERSION,
        "commit_sha": sha,
        "episode_id": episode_id,
        "trailer_present": bool(trailer_value),
        "trailer_value": trailer_value,
        "collected_at": _now(),
    }


def collect_witness(sha: str) -> dict:
    """Artifact 3: witness.json"""
    result = _run_json(["agentmesh", "witness", "verify", sha, "--json"])
    result["schema_version"] = SCHEMA_VERSION
    result["collected_at"] = _now()
    return result


def collect_weave() -> dict:
    """Artifact 4: weave_chain.json"""
    verify_proc = _run(["agentmesh", "weave", "verify"])
    export_proc = _run(["agentmesh", "weave", "export"])

    integrity = "pass" if verify_proc.returncode == 0 else "fail"
    try:
        chain = json.loads(export_proc.stdout) if export_proc.stdout.strip() else []
    except json.JSONDecodeError:
        chain = []

    return {
        "schema_version": SCHEMA_VERSION,
        "integrity": integrity,
        "gaps": 0 if integrity == "pass" else 1,
        "chain_length": len(chain) if isinstance(chain, list) else 0,
        "collected_at": _now(),
    }


def collect_ci_run(pr: int) -> dict:
    """Artifact 5: ci_run.json"""
    proc = _run(["gh", "pr", "checks", str(pr), "--json", "name,state,bucket"])
    if proc.returncode != 0:
        return {
            "schema_version": SCHEMA_VERSION,
            "pr_number": pr,
            "error": f"gh pr checks failed: {proc.stderr.strip()}",
            "all_required_passed": False,
            "missing_required": sorted(REQUIRED_CHECKS),
            "collected_at": _now(),
        }

    try:
        checks = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {
            "schema_version": SCHEMA_VERSION,
            "pr_number": pr,
            "error": "gh pr checks returned non-JSON",
            "all_required_passed": False,
            "missing_required": sorted(REQUIRED_CHECKS),
            "collected_at": _now(),
        }

    required_results = {}
    all_checks = []
    failed = []

    for check in checks:
        name = check.get("name", "")
        state = check.get("state", "")
        bucket = check.get("bucket", "")
        all_checks.append({"name": name, "state": state, "bucket": bucket})
        if name in REQUIRED_CHECKS:
            required_results[name] = bucket
        if bucket not in ("pass", ""):
            failed.append(name)

    # Fail-closed: every required check must be present and passing
    missing_required = sorted(REQUIRED_CHECKS - set(required_results.keys()))
    all_passed = (
        not missing_required
        and len(required_results) == len(REQUIRED_CHECKS)
        and all(v == "pass" for v in required_results.values())
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "pr_number": pr,
        "required_checks": required_results,
        "missing_required": missing_required,
        "all_required_passed": all_passed,
        "failed_checks": failed,
        "total_checks": len(all_checks),
        "checks": all_checks,
        "collected_at": _now(),
    }


def collect_assay_gate() -> dict:
    """Artifact 6: assay_gate.json -- native bridge emission."""
    result = _run_json(["agentmesh", "bridge", "emit",
                        "--task-id", "canary",
                        "--terminal-state", "MERGED"])
    result["collected_at"] = _now()
    return result


def _extract_run_id_from_link(link: str) -> str:
    """Extract workflow run ID from a GitHub Actions job link."""
    # link format: https://github.com/.../actions/runs/<run_id>/job/<job_id>
    if "/actions/runs/" not in link:
        return ""
    parts = link.split("/actions/runs/")[1].split("/")
    return parts[0] if parts else ""


def _try_structured_artifact(run_id: str) -> dict | None:
    """Tier 1: Download the structured trust_result.json from CI artifacts."""
    if not run_id:
        return None
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        proc = _run(["gh", "run", "download", run_id,
                      "--name", "assay-verify-report",
                      "--dir", tmpdir])
        if proc.returncode != 0:
            return None
        trust_path = Path(tmpdir) / "trust_result.json"
        if not trust_path.exists():
            return None
        try:
            data = json.loads(trust_path.read_text())
        except (json.JSONDecodeError, ValueError):
            return None
        # Validate required fields
        if "trust_decision" not in data or "schema_version" not in data:
            return None
        data["source"] = "ci_structured_artifact"
        return data


def _try_log_scrape(job_id: str) -> dict | None:
    """Tier 2: Extract trust decision from CI job logs (fallback)."""
    if not job_id:
        return None
    log_proc = _run(["gh", "api", f"repos/:owner/:repo/actions/jobs/{job_id}/logs"])
    if log_proc.returncode != 0:
        return None

    trust_decision = None
    fingerprint_verified = False
    ci_evidence: dict = {}

    for line in log_proc.stdout.splitlines():
        if "Trust decision:" in line:
            parts = line.split("Trust decision:")
            if len(parts) == 2:
                raw = parts[1].strip()
                # Map to canonical enum
                enum_map = {"accept": "ACCEPT", "reject": "REJECT"}
                trust_decision = enum_map.get(raw, "INCONCLUSIVE")
        if "Fingerprint verified:" in line:
            fingerprint_verified = True
            parts = line.split("Fingerprint verified:")
            if len(parts) == 2:
                ci_evidence["fingerprint_prefix"] = parts[1].strip()
        if "signers.yaml hash=" in line:
            parts = line.split("signers.yaml hash=")
            if len(parts) == 2:
                ci_evidence["signers_yaml_hash"] = parts[1].strip()

    if trust_decision is None:
        return None

    return {
        "schema_version": SCHEMA_VERSION,
        "trust_decision": trust_decision,
        "source": "ci_job_logs",
        "fingerprint_verified": fingerprint_verified,
        "ci_evidence": ci_evidence,
    }


def collect_trust_evaluation(pr: int) -> dict:
    """Artifact 7: trust_evaluation.json

    Three-tier precedence:
      1. CI structured artifact (trust_result.json) — declared evidence
      2. CI job log scraping — forensic inference (fallback)
      3. INCONCLUSIVE — never overclaims

    Trust decision enum: ACCEPT | REJECT | INCONCLUSIVE
    """
    # Find the assay-verify job for this PR
    proc = _run(["gh", "pr", "checks", str(pr), "--json", "name,state,bucket,link"])
    try:
        checks = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        return {
            "schema_version": SCHEMA_VERSION,
            "trust_decision": "INCONCLUSIVE",
            "reason": "could not fetch PR checks",
            "collected_at": _now(),
        }

    verify_check = next((c for c in checks if c.get("name") == "assay-verify"), None)
    if not verify_check:
        return {
            "schema_version": SCHEMA_VERSION,
            "trust_decision": "INCONCLUSIVE",
            "reason": "assay-verify check not found in PR checks",
            "collected_at": _now(),
        }

    link = verify_check.get("link", "")
    job_id = link.rsplit("/", 1)[-1] if "/job/" in link else ""
    run_id = _extract_run_id_from_link(link)
    bucket = verify_check.get("bucket", "")

    # If the check itself didn't pass, distinguish REJECT from INCONCLUSIVE
    if bucket != "pass":
        return {
            "schema_version": SCHEMA_VERSION,
            "trust_decision": "INCONCLUSIVE",
            "reason": f"assay-verify check did not pass (state={verify_check.get('state')}, "
                      f"bucket={bucket}). Could be policy rejection or infra failure.",
            "ci_check_link": link,
            "collected_at": _now(),
        }

    # Tier 1: structured CI artifact
    result = _try_structured_artifact(run_id)
    if result:
        result["ci_job_id"] = job_id
        result["ci_check_link"] = link
        result["collected_at"] = _now()
        return result

    # Tier 2: log scraping
    result = _try_log_scrape(job_id)
    if result:
        result["ci_job_id"] = job_id
        result["ci_check_link"] = link
        result["collected_at"] = _now()
        return result

    # Tier 3: INCONCLUSIVE
    return {
        "schema_version": SCHEMA_VERSION,
        "trust_decision": "INCONCLUSIVE",
        "reason": "could not extract trust decision from CI artifact or logs",
        "ci_job_id": job_id,
        "ci_check_link": link,
        "collected_at": _now(),
    }


def collect_merge_decision(pr: int, pr_info: dict) -> dict:
    """Artifact 8: merge_decision.json (post-merge only)."""
    merge_commit = pr_info.get("mergeCommit") or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "pr_number": pr,
        "pr_url": pr_info["url"],
        "state": pr_info["state"],
        "merged_at": pr_info.get("mergedAt", ""),
        "merged_sha": merge_commit.get("oid", ""),
        "missing_artifacts": [],
        "collected_at": _now(),
    }


def write_manifest(out_dir: Path, episode_id: str) -> dict:
    """Artifact 9: bundle_manifest.json -- SHA-256 of all other artifacts."""
    files = {}
    for f in sorted(out_dir.glob("*.json")):
        if f.name == "bundle_manifest.json":
            continue
        files[f.name] = _sha256(f)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "episode_id": episode_id,
        "files": files,
        "file_count": len(files),
        "created_at": _now(),
    }
    (out_dir / "bundle_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def write_artifact(out_dir: Path, name: str, data: dict) -> None:
    path = out_dir / name
    path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"  {name}: written")


def main():
    parser = argparse.ArgumentParser(description="Collect canary artifact bundle")
    parser.add_argument("--pr", type=int, required=True, help="PR number")
    parser.add_argument("--phase", choices=["pre", "post", "full"], default="full",
                        help="Collection phase")
    parser.add_argument("--out", type=str, default="", help="Output directory override")
    args = parser.parse_args()

    pr_info = get_pr_info(args.pr)
    head_sha = pr_info["headRefOid"]
    episode_id = get_episode_from_commit(head_sha)

    if not episode_id:
        print(f"Warning: no AgentMesh-Episode trailer on {head_sha[:10]}", file=sys.stderr)
        episode_id = f"unknown_{head_sha[:12]}"

    out_dir = Path(args.out) if args.out else Path(f"artifacts/canary/{episode_id}")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Collecting canary artifacts for PR #{args.pr} -> {out_dir}/")
    print(f"  episode: {episode_id}")
    print(f"  head: {head_sha[:10]}")
    print()

    if args.phase in ("pre", "full"):
        print("--- Pre-merge artifacts ---")
        write_artifact(out_dir, "episode.json", collect_episode(episode_id, pr_info))
        write_artifact(out_dir, "lineage.json", collect_lineage(episode_id, pr_info))
        write_artifact(out_dir, "witness.json", collect_witness(head_sha))
        write_artifact(out_dir, "weave_chain.json", collect_weave())
        write_artifact(out_dir, "ci_run.json", collect_ci_run(args.pr))
        write_artifact(out_dir, "assay_gate.json", collect_assay_gate())
        write_artifact(out_dir, "trust_evaluation.json", collect_trust_evaluation(args.pr))
        print()

    if args.phase in ("post", "full"):
        if pr_info["state"] != "MERGED":
            print(f"Warning: PR is {pr_info['state']}, not MERGED. "
                  f"Merge decision may be incomplete.", file=sys.stderr)
        print("--- Post-merge artifacts ---")
        # Refresh PR info for merge data
        pr_info = get_pr_info(args.pr)
        write_artifact(out_dir, "merge_decision.json", collect_merge_decision(args.pr, pr_info))
        print()

    print("--- Manifest ---")
    manifest = write_manifest(out_dir, episode_id)
    print(f"  bundle_manifest.json: {manifest['file_count']} files hashed")
    print()

    # Verify
    print("--- Integrity check ---")
    all_ok = True
    for name, expected_hash in manifest["files"].items():
        actual = _sha256(out_dir / name)
        ok = actual == expected_hash
        status = "OK" if ok else "MISMATCH"
        print(f"  {name}: {status}")
        if not ok:
            all_ok = False

    print()
    if all_ok:
        print(f"Bundle complete: {manifest['file_count'] + 1} artifacts in {out_dir}/")
    else:
        print("INTEGRITY FAILURE: hash mismatch detected", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
