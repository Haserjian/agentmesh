"""Bridge: emit ASSAY_RECEIPT events on terminal task transitions.

Assay is an optional dependency. The bridge runs ``assay gate check`` via
subprocess and records the result (or a degraded reason) into the hash-chained
event log. Two outcomes only:

* ``BRIDGE_EMIT_OK`` -- assay ran and produced a gate report.
* ``BRIDGE_EMIT_DEGRADED`` -- assay unavailable, errored, or no repo path found.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import db, events
from .models import EventKind

_OK = "BRIDGE_EMIT_OK"
_DEGRADED = "BRIDGE_EMIT_DEGRADED"


@dataclass(frozen=True)
class BridgeResult:
    status: str  # _OK | _DEGRADED
    gate_report: dict[str, Any]
    reason: str  # empty on OK, human-readable on degraded


def _find_repo_path(task_id: str, data_dir: Path | None) -> Path | None:
    """Look up repo_cwd from spawn records for *task_id*.

    Falls back to the process CWD if it looks like a git repo.  This
    handles CLI-driven flows (``orch advance --to merged``) where no
    spawn record exists.
    """
    try:
        rows = db.list_spawns_db(data_dir=data_dir)
    except Exception:
        rows = []
    matches = [r for r in rows if r.get("task_id") == task_id]
    if matches:
        cwd = matches[-1].get("repo_cwd", "")
        if cwd:
            return Path(cwd)

    # Fallback: CWD if it contains a .git directory (CLI-driven flows).
    try:
        cwd = Path.cwd()
        if (cwd / ".git").is_dir():
            return cwd
    except OSError:
        pass

    return None


def _run_assay_gate(repo_path: Path) -> tuple[str, dict[str, Any], str]:
    """Run ``assay gate check`` and return (status, report, reason)."""
    if shutil.which("assay") is None:
        return _DEGRADED, {}, "assay CLI not found on PATH"

    try:
        proc = subprocess.run(
            ["assay", "gate", "check", str(repo_path), "--min-score", "0", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return _DEGRADED, {}, "assay gate check timed out"
    except OSError as exc:
        return _DEGRADED, {}, f"failed to start assay: {exc}"

    if proc.returncode == 3:
        return _DEGRADED, {}, "assay gate check: bad input"

    try:
        report = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        return _DEGRADED, {}, "assay returned non-JSON output"

    # Exit 0 (PASS) or 1 (FAIL) are both valid gate results.
    return _OK, report, ""


def emit_bridge_event(
    *,
    task_id: str,
    terminal_state: str,
    repo_path: Path | None = None,
    agent_id: str = "",
    episode_id: str = "",
    data_dir: Path | None = None,
) -> BridgeResult:
    """Run assay gate check and emit an ``ASSAY_RECEIPT`` event.

    Always emits -- never raises, never silently skips.
    """
    if repo_path is None:
        repo_path = _find_repo_path(task_id, data_dir)

    if repo_path is None or not repo_path.is_dir():
        status, gate_report, reason = (
            _DEGRADED,
            {},
            "no repo path found for task",
        )
    else:
        status, gate_report, reason = _run_assay_gate(repo_path)

    payload: dict[str, Any] = {
        "task_id": task_id,
        "terminal_state": terminal_state,
        "bridge_status": status,
        "gate_report": gate_report,
        # Evidence Wire Protocol v0 envelope
        "_ewp_version": "0",
        "_ewp_task_id": task_id,
        "_ewp_origin": "agentmesh/assay_bridge",
    }
    if episode_id:
        payload["_ewp_episode_id"] = episode_id
    if agent_id:
        payload["_ewp_agent_id"] = agent_id
    if reason:
        payload["degraded_reason"] = reason

    events.append_event(
        kind=EventKind.ASSAY_RECEIPT,
        agent_id=agent_id,
        payload=payload,
        data_dir=data_dir,
    )

    return BridgeResult(status=status, gate_report=gate_report, reason=reason)
