"""Deterministic context capsule builder from git + mesh state."""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

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


def build_capsule(
    agent_id: str,
    task_desc: str = "",
    cwd: str | None = None,
    data_dir: Path | None = None,
) -> Capsule:
    """Build a context capsule from git state + mesh state."""
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
        created_at=now,
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
