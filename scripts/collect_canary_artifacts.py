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
    proc = _run(["gh", "pr", "checks", str(pr), "--json",
                 "name,state,conclusion,detailsUrl,bucket"])
    if proc.returncode != 0:
        # gh pr checks --json may not be available; fall back to API
        proc = _run(["gh", "api", f"repos/:owner/:repo/pulls/{pr}/checks"])

    try:
        checks = json.loads(proc.stdout)
    except json.JSONDecodeError:
        checks = []

    # Normalize: gh pr checks --json returns list of dicts
    required_results = {}
    all_checks = []
    failed = []

    for check in checks:
        name = check.get("name", "")
        conclusion = check.get("conclusion", check.get("state", ""))
        all_checks.append({"name": name, "conclusion": conclusion})
        if name in REQUIRED_CHECKS:
            required_results[name] = conclusion
        if conclusion not in ("success", "pass", "skipped", ""):
            failed.append(name)

    return {
        "schema_version": SCHEMA_VERSION,
        "pr_number": pr,
        "required_checks": required_results,
        "all_required_passed": all(
            v in ("success", "pass") for v in required_results.values()
        ),
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


def collect_trust_evaluation() -> dict:
    """Artifact 7: trust_evaluation.json"""
    # Try local trust evaluation; degrade gracefully
    proc = _run(["assay", "verify-pack", "--help"])
    has_assay = proc.returncode == 0

    if not has_assay:
        return {
            "schema_version": SCHEMA_VERSION,
            "trust_decision": "SKIP",
            "reason": "assay CLI not available locally",
            "collected_at": _now(),
        }

    # Check if trust policy dir exists
    trust_dir = Path("trust")
    if not trust_dir.exists():
        return {
            "schema_version": SCHEMA_VERSION,
            "trust_decision": "SKIP",
            "reason": "no trust/ policy directory in repo",
            "collected_at": _now(),
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "trust_decision": "DEFERRED_TO_CI",
        "reason": "trust gate enforcement runs in CI with bootstrapped signer overlay; "
                  "local evaluation deferred. Check ci_run.json assay-verify result.",
        "ci_trust_gate_active": True,
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
        write_artifact(out_dir, "trust_evaluation.json", collect_trust_evaluation())
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
