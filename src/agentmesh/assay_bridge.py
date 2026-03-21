"""Bridge: emit ASSAY_RECEIPT events on terminal task transitions.

Assay is an optional dependency. The bridge runs ``assay gate check`` via
subprocess and records the result (or a degraded reason) into the hash-chained
event log. Two outcomes only:

* ``BRIDGE_EMIT_OK`` -- assay ran and produced a gate report.
* ``BRIDGE_EMIT_DEGRADED`` -- assay unavailable, errored, or no repo path found.

CCOI adoption (v0.1b):
    This is the first non-advisory cross-organ envelope seam.  agentmesh
    (source) calls assay-toolkit (target) with AUDITING authority via
    subprocess.  The envelope is a dict following CCOI_V0_1.md §4.1 —
    no import from ccio.  The protocol is proven by shape, not by shared
    library coupling.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import db, events
from .models import EventKind

_OK = "BRIDGE_EMIT_OK"
_DEGRADED = "BRIDGE_EMIT_DEGRADED"

# CCOI envelope required fields (CCOI_V0_1.md §4.1).
# This is the wire contract — no ccio import needed.
CCOI_ENVELOPE_REQUIRED_FIELDS = frozenset({
    "ccoi_version",
    "source_organ",
    "target_organ",
    "authority_class",
    "primitive",
    "correlation_id",
    "ts_sent",
    "payload",
})


def _build_ccoi_envelope(
    *,
    task_id: str,
    terminal_state: str,
    bridge_status: str,
    gate_report: dict[str, Any],
    agent_id: str = "",
    episode_id: str = "",
    degraded_reason: str = "",
) -> dict[str, Any]:
    """Build a CCOI-shaped envelope dict for this bridge seam.

    Pure function, no I/O, no ccio dependency.  Follows CCOI_V0_1.md §4.1
    with AUDITING authority and QUERY primitive.

    The envelope is emitted on both OK and DEGRADED paths — transport
    metadata survives execution failure.
    """
    envelope: dict[str, Any] = {
        "ccoi_version": "0.1",
        "source_organ": "agentmesh",
        "target_organ": "assay-toolkit",
        "authority_class": "AUDITING",
        "primitive": "QUERY",
        "correlation_id": task_id,
        "ts_sent": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "payload": {
            "bridge": "assay_bridge",
            "action": "gate_check",
            "terminal_state": terminal_state,
            "bridge_status": bridge_status,
            "gate_report": gate_report,
        },
    }
    if agent_id:
        envelope["payload"]["agent_id"] = agent_id
    if episode_id:
        envelope["payload"]["episode_id"] = episode_id
    if degraded_reason:
        envelope["payload"]["degraded_reason"] = degraded_reason
    return envelope


@dataclass(frozen=True)
class BridgeResult:
    status: str  # _OK | _DEGRADED
    gate_report: dict[str, Any]
    reason: str  # empty on OK, human-readable on degraded
    envelope: dict[str, Any] | None = None  # CCOI envelope when emitted


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

    # Build CCOI envelope — always, on both OK and DEGRADED paths.
    envelope = _build_ccoi_envelope(
        task_id=task_id,
        terminal_state=terminal_state,
        bridge_status=status,
        gate_report=gate_report,
        agent_id=agent_id,
        episode_id=episode_id,
        degraded_reason=reason,
    )

    payload: dict[str, Any] = {
        "task_id": task_id,
        "terminal_state": terminal_state,
        "bridge_status": status,
        "gate_report": gate_report,
        # CCOI envelope (v0.1 §4.1) — replaces EWP v0
        "ccoi_envelope": envelope,
    }
    if episode_id:
        payload["episode_id"] = episode_id
    if agent_id:
        payload["agent_id"] = agent_id
    if reason:
        payload["degraded_reason"] = reason

    events.append_event(
        kind=EventKind.ASSAY_RECEIPT,
        agent_id=agent_id,
        payload=payload,
        data_dir=data_dir,
    )

    return BridgeResult(status=status, gate_report=gate_report, reason=reason, envelope=envelope)


# ---------------------------------------------------------------------------
# Proof Posture bridge
# ---------------------------------------------------------------------------

_POSTURE_OK = "POSTURE_OK"
_POSTURE_DEGRADED = "POSTURE_DEGRADED"


@dataclass(frozen=True)
class PostureResult:
    status: str  # _POSTURE_OK | _POSTURE_DEGRADED
    posture: dict[str, Any]  # Full posture dict from assay posture --json
    text: str  # Human-readable rendered summary
    reason: str  # Empty on OK, human-readable on degraded


def _find_proof_packs(repo_path: Path) -> list[Path]:
    """Find proof pack directories in a repo, newest first."""
    packs: list[Path] = []
    for candidate in repo_path.iterdir():
        if candidate.is_dir() and candidate.name.startswith("proof_pack_"):
            if (candidate / "pack_manifest.json").exists():
                packs.append(candidate)
    return sorted(packs, key=lambda p: p.stat().st_mtime, reverse=True)


def _run_assay_posture(
    pack_dir: Path,
    *,
    require_falsifiers: bool = False,
) -> tuple[str, dict[str, Any], str, str]:
    """Run ``assay posture`` and return (status, posture_dict, text, reason)."""
    if shutil.which("assay") is None:
        return _POSTURE_DEGRADED, {}, "", "assay CLI not found on PATH"

    cmd = ["assay", "posture", str(pack_dir), "--json"]
    if require_falsifiers:
        cmd.append("--require-falsifiers")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return _POSTURE_DEGRADED, {}, "", "assay posture timed out"
    except OSError as exc:
        return _POSTURE_DEGRADED, {}, "", f"failed to start assay: {exc}"

    if proc.returncode == 3:
        return _POSTURE_DEGRADED, {}, "", f"assay posture: bad input ({proc.stderr.strip()})"

    try:
        posture = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        return _POSTURE_DEGRADED, {}, "", "assay posture returned non-JSON output"

    # Also get the text version for PR comments
    text_cmd = ["assay", "posture", str(pack_dir)]
    if require_falsifiers:
        text_cmd.append("--require-falsifiers")
    try:
        text_proc = subprocess.run(
            text_cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        text = text_proc.stdout.strip()
    except Exception:
        text = ""

    # Exit 0 (verified/supported) and 1 (incomplete/blocked) are both valid.
    return _POSTURE_OK, posture, text, ""


def _post_pr_comment(pr_ref: str, body: str, repo_path: Path) -> bool:
    """Post a comment to a PR via ``gh pr comment``. Returns True on success."""
    if shutil.which("gh") is None:
        return False
    try:
        subprocess.run(
            ["gh", "pr", "comment", pr_ref, "--body", body],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(repo_path),
        )
        return True
    except Exception:
        return False


def emit_posture_comment(
    *,
    task_id: str,
    pr_ref: str,
    repo_path: Path | None = None,
    pack_dir: Path | None = None,
    require_falsifiers: bool = False,
    agent_id: str = "",
    episode_id: str = "",
    data_dir: Path | None = None,
) -> PostureResult:
    """Run proof posture and post a PR comment.

    AgentMesh calls Assay, does not reinterpret Assay.
    Assay owns posture semantics. AgentMesh owns when/where to attach.
    """
    # Resolve repo path
    if repo_path is None:
        repo_path = _find_repo_path(task_id, data_dir)
    if repo_path is None or not repo_path.is_dir():
        return PostureResult(
            status=_POSTURE_DEGRADED,
            posture={},
            text="",
            reason="no repo path found for task",
        )

    # Resolve proof pack
    if pack_dir is None:
        packs = _find_proof_packs(repo_path)
        if not packs:
            return PostureResult(
                status=_POSTURE_DEGRADED,
                posture={},
                text="",
                reason=f"no proof packs found in {repo_path}",
            )
        pack_dir = packs[0]  # newest

    # Run assay posture
    status, posture, text, reason = _run_assay_posture(
        pack_dir, require_falsifiers=require_falsifiers,
    )

    if status == _POSTURE_DEGRADED:
        result = PostureResult(status=status, posture=posture, text=text, reason=reason)
    else:
        # Format the PR comment
        disposition = posture.get("disposition", "unknown")
        header = f"**Proof Posture: {disposition.upper().replace('_', ' ')}**"
        comment_body = f"{header}\n\n```\n{text}\n```\n\n<sub>Generated by assay posture | pack: {pack_dir.name}</sub>"

        posted = _post_pr_comment(pr_ref, comment_body, repo_path)

        result = PostureResult(
            status=_POSTURE_OK,
            posture=posture,
            text=text,
            reason="" if posted else "posture computed but gh pr comment failed",
        )

    # Emit event
    events.append_event(
        kind=EventKind.ASSAY_RECEIPT,
        agent_id=agent_id,
        payload={
            "task_id": task_id,
            "action": "posture_comment",
            "pr_ref": pr_ref,
            "bridge_status": result.status,
            "disposition": posture.get("disposition", ""),
            "pack_dir": str(pack_dir) if pack_dir else "",
            "degraded_reason": result.reason,
        },
        data_dir=data_dir,
    )

    return result
