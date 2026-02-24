"""Higher-level claim logic: path normalization, conflict reporting."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import Claim, ClaimIntent, ClaimState, EventKind, _now
from . import db, events


def normalize_path(path: str) -> str:
    """Normalize a file path to absolute form."""
    return str(Path(path).resolve())


def make_claim(
    agent_id: str,
    path: str,
    intent: ClaimIntent = ClaimIntent.EDIT,
    ttl_s: int = 1800,
    reason: str = "",
    force: bool = False,
    data_dir: Path | None = None,
) -> tuple[bool, Claim, list[Claim]]:
    """Create a claim with conflict detection.

    Returns (success, claim, conflicts).
    """
    norm = normalize_path(path)
    now_str = _now()
    now_dt = datetime.now(timezone.utc)
    expires = (now_dt + timedelta(seconds=ttl_s)).isoformat()
    claim_id = f"clm_{uuid.uuid4().hex[:12]}"

    claim = Claim(
        claim_id=claim_id, agent_id=agent_id, path=norm,
        intent=intent, state=ClaimState.ACTIVE,
        ttl_s=ttl_s, created_at=now_str, expires_at=expires,
        reason=reason,
    )

    success, conflicts = db.check_and_claim(claim, force=force, data_dir=data_dir)

    if success:
        events.append_event(
            EventKind.CLAIM, agent_id=agent_id,
            payload={"claim_id": claim_id, "path": norm, "intent": intent.value, "ttl_s": ttl_s},
            data_dir=data_dir,
        )

    return success, claim, conflicts


def release(
    agent_id: str,
    path: str | None = None,
    release_all: bool = False,
    data_dir: Path | None = None,
) -> int:
    """Release claims. Returns count released."""
    norm = normalize_path(path) if path else None
    count = db.release_claim(agent_id, norm, release_all=release_all, data_dir=data_dir)
    if count > 0:
        events.append_event(
            EventKind.RELEASE, agent_id=agent_id,
            payload={"path": norm, "all": release_all, "count": count},
            data_dir=data_dir,
        )
    return count


def check(path: str, exclude_agent: str | None = None,
          data_dir: Path | None = None) -> list[Claim]:
    """Check for active edit claims on a path."""
    norm = normalize_path(path)
    # Expire stale first
    db.expire_stale_claims(data_dir)
    return db.check_collision(norm, exclude_agent=exclude_agent, data_dir=data_dir)


def format_conflict(conflicts: list[Claim]) -> str:
    """Format conflict list for display."""
    if not conflicts:
        return "No conflicts"
    lines = []
    for c in conflicts:
        lines.append(f"  CONFLICT: {c.path} claimed by {c.agent_id} "
                     f"(intent={c.intent.value}, expires={c.expires_at})")
    return "\n".join(lines)
