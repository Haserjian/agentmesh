"""Higher-level claim logic: path normalization, conflict reporting."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import Claim, ClaimIntent, ClaimState, EventKind, ResourceType, _now
from . import db, events

_RESOURCE_PREFIXES = {rt.name for rt in ResourceType if rt != ResourceType.FILE}


def normalize_path(path: str) -> str:
    """Normalize a file path to absolute form."""
    return str(Path(path).resolve())


def parse_resource_string(resource: str) -> tuple[ResourceType, str]:
    """Parse a resource string into (ResourceType, identifier).

    "PORT:3000"              -> (PORT, "3000")
    "LOCK:npm"               -> (LOCK, "npm")
    "TEST_SUITE:integration" -> (TEST_SUITE, "integration")
    "TEMP_DIR:/tmp/ws"       -> (TEMP_DIR, "/tmp/ws")
    "/tmp/foo.py"            -> (FILE, normalized_path)
    "bare_name"              -> (FILE, normalized_path)
    """
    colon_idx = resource.find(":")
    if colon_idx > 0:
        prefix = resource[:colon_idx].upper()
        if prefix in _RESOURCE_PREFIXES:
            value = resource[colon_idx + 1:]
            rt = ResourceType(prefix.lower())
            if rt == ResourceType.TEMP_DIR:
                value = normalize_path(value)
            return rt, value
    return ResourceType.FILE, normalize_path(resource)


def make_claim(
    agent_id: str,
    path: str,
    intent: ClaimIntent = ClaimIntent.EDIT,
    ttl_s: int = 1800,
    reason: str = "",
    force: bool = False,
    resource_type: ResourceType | None = None,
    episode_id: str | None = None,
    priority: int = 5,
    data_dir: Path | None = None,
) -> tuple[bool, Claim, list[Claim]]:
    """Create a claim with conflict detection.

    Returns (success, claim, conflicts).
    If resource_type is None, parses from path string (e.g. "PORT:3000").
    If episode_id is None, auto-reads current episode.
    """
    if resource_type is not None:
        rt = resource_type
        norm = normalize_path(path) if rt == ResourceType.FILE else path
    else:
        rt, norm = parse_resource_string(path)

    # Auto-tag with current episode
    if episode_id is None:
        from .episodes import get_current_episode
        episode_id = get_current_episode(data_dir)

    now_str = _now()
    now_dt = datetime.now(timezone.utc)
    expires = (now_dt + timedelta(seconds=ttl_s)).isoformat()
    claim_id = f"clm_{uuid.uuid4().hex[:12]}"

    claim = Claim(
        claim_id=claim_id, agent_id=agent_id, path=norm,
        resource_type=rt, intent=intent, state=ClaimState.ACTIVE,
        ttl_s=ttl_s, created_at=now_str, expires_at=expires,
        reason=reason, episode_id=episode_id, priority=priority,
        effective_priority=priority,
    )

    success, conflicts = db.check_and_claim(claim, force=force, data_dir=data_dir)

    if success:
        events.append_event(
            EventKind.CLAIM, agent_id=agent_id,
            payload={
                "claim_id": claim_id, "path": norm, "resource_type": rt.value,
                "intent": intent.value, "ttl_s": ttl_s,
                "episode_id": episode_id,
            },
            data_dir=data_dir,
        )

    return success, claim, conflicts


def release(
    agent_id: str,
    path: str | None = None,
    resource_type: ResourceType | None = None,
    release_all: bool = False,
    data_dir: Path | None = None,
) -> int:
    """Release claims. Returns count released."""
    if path and resource_type is None:
        rt, norm = parse_resource_string(path)
    elif path:
        rt = resource_type
        norm = normalize_path(path) if rt == ResourceType.FILE else path
    else:
        rt = ResourceType.FILE
        norm = None

    count = db.release_claim(
        agent_id, norm, resource_type=rt, release_all=release_all, data_dir=data_dir,
    )
    if count > 0:
        events.append_event(
            EventKind.RELEASE, agent_id=agent_id,
            payload={"path": norm, "resource_type": rt.value, "all": release_all, "count": count},
            data_dir=data_dir,
        )
    return count


def check(path: str, resource_type: ResourceType | None = None,
          exclude_agent: str | None = None,
          data_dir: Path | None = None) -> list[Claim]:
    """Check for active edit claims on a resource."""
    if resource_type is None:
        rt, norm = parse_resource_string(path)
    else:
        rt = resource_type
        norm = normalize_path(path) if rt == ResourceType.FILE else path
    db.expire_stale_claims(data_dir)
    return db.check_collision(norm, resource_type=rt, exclude_agent=exclude_agent, data_dir=data_dir)


def format_conflict(conflicts: list[Claim]) -> str:
    """Format conflict list for display."""
    if not conflicts:
        return "No conflicts"
    lines = []
    for c in conflicts:
        lines.append(f"  CONFLICT: {c.path} claimed by {c.agent_id} "
                     f"(intent={c.intent.value}, expires={c.expires_at})")
    return "\n".join(lines)
