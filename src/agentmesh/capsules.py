"""Deterministic context capsule builder from git + mesh state."""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from pathlib import Path
from typing import Any

from .models import Capsule, EventKind, _now
from . import db, events

_DEFAULT_DIR = Path.home() / ".agentmesh"


def _run_git(args: list[str], cwd: str | None = None) -> str:
    """Run a git command and return stdout. Returns empty string on failure."""
    try:
        result = subprocess.run(
            ["git"] + args, capture_output=True, text=True, timeout=10,
            cwd=cwd,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def _file_hash(path: str) -> str:
    """Quick SHA256 of a file, empty string if unreadable."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]
    except (OSError, ValueError):
        return ""


def _build_sbar(
    *,
    task_desc: str,
    branch: str,
    sha: str,
    now: str,
    files_changed: list[str],
    diff_stat: str,
    agent_claims: list,
    work_dir: str,
) -> dict[str, Any]:
    """Build SBAR (Situation/Background/Assessment/Recommendation) dict."""
    changed_file_entries = []
    for f in files_changed:
        full = str(Path(work_dir).resolve() / f) if not Path(f).is_absolute() else f
        changed_file_entries.append({
            "path": f,
            "post_hash": _file_hash(full),
        })

    open_claim_summaries = []
    for c in agent_claims:
        open_claim_summaries.append({
            "resource_type": c.resource_type.value,
            "path": c.path,
            "intent": c.intent.value,
            "expires_at": c.expires_at,
        })

    return {
        "situation": {
            "global_objective": task_desc,
            "git_head": f"{branch}@{sha}" if branch and sha else "",
            "snapshot_time": now,
        },
        "background": {
            "changed_files": changed_file_entries,
            "diff_summary": diff_stat,
        },
        "assessment": {
            "test_status": "unknown",
            "open_claims": open_claim_summaries,
        },
        "recommendation": {
            "next_actions": [],
            "claim_resources": [c.path for c in agent_claims],
            "blockers": [],
        },
    }


def build_capsule(
    agent_id: str,
    task_desc: str = "",
    cwd: str | None = None,
    episode_id: str | None = None,
    data_dir: Path | None = None,
) -> Capsule:
    """Build a context capsule from git state + mesh state. Auto-tags with current episode."""
    # Auto-tag with current episode
    if episode_id is None:
        from .episodes import get_current_episode
        episode_id = get_current_episode(data_dir)

    work_dir = cwd or "."

    # Git state
    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=work_dir)
    sha = _run_git(["rev-parse", "--short", "HEAD"], cwd=work_dir)
    diff_stat = _run_git(["diff", "--stat"], cwd=work_dir)
    status_out = _run_git(["status", "--porcelain"], cwd=work_dir)
    files_changed = [line.split()[-1] for line in status_out.splitlines() if line.strip()]

    # Mesh state
    agent_claims = db.list_claims(data_dir, agent_id=agent_id, active_only=True)
    recent_msgs = db.list_messages(data_dir, limit=5)
    active_agents = db.list_agents(data_dir)

    capsule_id = f"cap_{uuid.uuid4().hex[:12]}"
    now = _now()

    sbar = _build_sbar(
        task_desc=task_desc, branch=branch, sha=sha, now=now,
        files_changed=files_changed, diff_stat=diff_stat,
        agent_claims=agent_claims, work_dir=work_dir,
    )

    capsule = Capsule(
        capsule_id=capsule_id,
        agent_id=agent_id,
        task_desc=task_desc,
        git_branch=branch,
        git_sha=sha,
        diff_stat=diff_stat,
        files_changed=files_changed,
        test_status="unknown",
        test_summary="",
        what_changed="",
        what_remains="",
        risks=[],
        next_actions=[],
        sbar=sbar,
        created_at=now,
        episode_id=episode_id,
    )

    # Save to DB
    db.save_capsule(capsule, data_dir)

    # Save to disk as JSON bundle
    bundle_dir = (data_dir or _DEFAULT_DIR) / "bundles"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    bundle = {
        "capsule_id": capsule_id,
        "agent_id": agent_id,
        "created_at": now,
        "task_desc": task_desc,
        "episode_id": episode_id,
        "git": {
            "branch": branch,
            "sha": sha,
            "diff_stat": diff_stat,
            "files_changed": files_changed,
        },
        "mesh": {
            "open_claims": [c.model_dump() for c in agent_claims],
            "recent_messages": [m.model_dump() for m in recent_msgs],
            "active_agents": [a.model_dump() for a in active_agents],
        },
        "sbar": sbar,
        "test": {"status": "unknown", "summary": ""},
        "summary": {
            "what_changed": "",
            "what_remains": "",
            "risks": [],
            "next_actions": [],
        },
    }
    bundle_path = bundle_dir / f"{capsule_id}.json"
    bundle_path.write_text(json.dumps(bundle, indent=2))

    # Event log
    events.append_event(
        EventKind.BUNDLE, agent_id=agent_id,
        payload={"capsule_id": capsule_id, "task": task_desc},
        data_dir=data_dir,
    )

    return capsule


def get_capsule_bundle(capsule_id: str, data_dir: Path | None = None) -> dict | None:
    """Load a capsule bundle from disk."""
    bundle_path = (data_dir or _DEFAULT_DIR) / "bundles" / f"{capsule_id}.json"
    if not bundle_path.exists():
        return None
    return json.loads(bundle_path.read_text())
