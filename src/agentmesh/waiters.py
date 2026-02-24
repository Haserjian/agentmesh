"""Waiters + priority inheritance -- real priority boost and stale-only steal."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Claim, ClaimIntent, ClaimState, ResourceType, Waiter, _now
from .episodes import get_current_episode
from . import db


def register_wait(
    agent_id: str,
    resource: str,
    priority: int = 5,
    reason: str = "",
    resource_type: ResourceType = ResourceType.FILE,
    data_dir: Path | None = None,
) -> Waiter:
    """Register a wait on a resource. Triggers priority inheritance on the holder."""
    if resource_type == ResourceType.FILE:
        from .claims import normalize_path
        resource = normalize_path(resource)

    episode_id = get_current_episode(data_dir)
    waiter_id = f"wait_{uuid.uuid4().hex[:12]}"

    waiter = Waiter(
        waiter_id=waiter_id,
        resource_path=resource,
        resource_type=resource_type,
        waiter_agent_id=agent_id,
        episode_id=episode_id,
        priority=priority,
        reason=reason,
        created_at=_now(),
    )
    db.add_waiter(waiter, data_dir)

    # Apply priority inheritance
    _apply_priority_inheritance(resource, resource_type, data_dir)

    return waiter


def _apply_priority_inheritance(
    resource_path: str,
    resource_type: ResourceType,
    data_dir: Path | None = None,
) -> None:
    """Set holder's effective_priority = max(own priority, max(waiter priorities))."""
    # Find active edit claim on this resource
    active_claims = db.check_collision(
        resource_path, resource_type=resource_type, data_dir=data_dir,
    )
    if not active_claims:
        return

    holder = active_claims[0]
    waiters = db.list_waiters(
        resource_path=resource_path, resource_type=resource_type, data_dir=data_dir,
    )
    if not waiters:
        return

    max_waiter_priority = max(w.priority for w in waiters)
    new_effective = max(holder.priority, max_waiter_priority)

    if new_effective != holder.effective_priority:
        db.update_effective_priority(holder.claim_id, new_effective, data_dir)


def steal_resource(
    agent_id: str,
    resource: str,
    reason: str = "",
    priority: int = 5,
    resource_type: ResourceType = ResourceType.FILE,
    stale_threshold_s: int = 300,
    data_dir: Path | None = None,
) -> tuple[bool, str]:
    """Attempt to steal a resource claim. Only succeeds if TTL expired or heartbeat stale.

    Returns (success, message).
    """
    if resource_type == ResourceType.FILE:
        from .claims import normalize_path
        resource = normalize_path(resource)

    episode_id = get_current_episode(data_dir)
    now_str = _now()
    now_dt = datetime.now(timezone.utc)
    expires = (now_dt + timedelta(seconds=1800)).isoformat()
    claim_id = f"clm_{uuid.uuid4().hex[:12]}"

    new_claim = Claim(
        claim_id=claim_id,
        agent_id=agent_id,
        path=resource,
        resource_type=resource_type,
        intent=ClaimIntent.EDIT,
        state=ClaimState.ACTIVE,
        ttl_s=1800,
        created_at=now_str,
        expires_at=expires,
        reason=reason,
        episode_id=episode_id,
        priority=priority,
        effective_priority=priority,
    )

    return db.steal_claim(
        stealer_agent_id=agent_id,
        new_claim=new_claim,
        stale_threshold_s=stale_threshold_s,
        data_dir=data_dir,
    )
