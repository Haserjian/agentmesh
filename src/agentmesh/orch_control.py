"""Orchestrator control primitives built on typed lock claims."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from . import claims, db
from .models import Agent, Claim, ClaimIntent, ResourceType

LEASE_PATH = "orchestration"
FREEZE_PATH = "orch_freeze"
MERGE_LOCK_PATH = "orch_lock_merges"

_DEFAULT_LEASE_TTL_S = 300
_CONTROL_TTL_S = 7 * 24 * 60 * 60


def make_owner(agent_hint: str = "") -> str:
    hint = (agent_hint or "orchestrator").replace(" ", "_")
    return f"orchctl_{hint}_{os.getpid()}_{uuid.uuid4().hex[:8]}"


def _ensure_owner_agent(owner: str, data_dir: Path | None = None) -> None:
    if db.get_agent(owner, data_dir) is not None:
        return
    db.register_agent(Agent(agent_id=owner, cwd=os.getcwd()), data_dir)


def acquire_lease(
    owner: str,
    data_dir: Path | None = None,
    ttl_s: int = _DEFAULT_LEASE_TTL_S,
    force: bool = False,
) -> tuple[bool, Claim, list[Claim]]:
    _ensure_owner_agent(owner, data_dir)
    return claims.make_claim(
        agent_id=owner,
        path=f"LOCK:{LEASE_PATH}",
        intent=ClaimIntent.EDIT,
        ttl_s=ttl_s,
        reason="orchestrator lease",
        force=force,
        data_dir=data_dir,
    )


def renew_lease(
    owner: str,
    data_dir: Path | None = None,
    ttl_s: int = _DEFAULT_LEASE_TTL_S,
) -> tuple[bool, Claim, list[Claim]]:
    """Renew lease for the same owner by re-claiming with fresh TTL."""
    return acquire_lease(owner=owner, data_dir=data_dir, ttl_s=ttl_s, force=False)


def release_lease(owner: str, data_dir: Path | None = None) -> int:
    return claims.release(
        agent_id=owner,
        path=LEASE_PATH,
        resource_type=ResourceType.LOCK,
        data_dir=data_dir,
    )


def _force_clear_resource(path: str, data_dir: Path | None = None) -> int:
    sweeper = make_owner("sweeper")
    _ensure_owner_agent(sweeper, data_dir)
    claims.make_claim(
        agent_id=sweeper,
        path=f"LOCK:{path}",
        intent=ClaimIntent.EDIT,
        ttl_s=5,
        reason="force clear resource",
        force=True,
        data_dir=data_dir,
    )
    return claims.release(
        agent_id=sweeper,
        path=path,
        resource_type=ResourceType.LOCK,
        data_dir=data_dir,
    )


def clear_lease(data_dir: Path | None = None) -> int:
    return _force_clear_resource(LEASE_PATH, data_dir=data_dir)


def lease_holders(data_dir: Path | None = None) -> list[Claim]:
    return claims.check(LEASE_PATH, resource_type=ResourceType.LOCK, data_dir=data_dir)


def set_frozen(
    enabled: bool,
    owner: str,
    data_dir: Path | None = None,
    reason: str = "",
) -> None:
    if enabled:
        _ensure_owner_agent(owner, data_dir)
        claims.make_claim(
            agent_id=owner,
            path=f"LOCK:{FREEZE_PATH}",
            intent=ClaimIntent.EDIT,
            ttl_s=_CONTROL_TTL_S,
            reason=reason or "orchestrator freeze",
            force=True,
            data_dir=data_dir,
        )
        return
    _force_clear_resource(FREEZE_PATH, data_dir=data_dir)


def is_frozen(data_dir: Path | None = None) -> bool:
    return bool(claims.check(FREEZE_PATH, resource_type=ResourceType.LOCK, data_dir=data_dir))


def freeze_holders(data_dir: Path | None = None) -> list[Claim]:
    return claims.check(FREEZE_PATH, resource_type=ResourceType.LOCK, data_dir=data_dir)


def set_merges_locked(
    enabled: bool,
    owner: str,
    data_dir: Path | None = None,
    reason: str = "",
) -> None:
    if enabled:
        _ensure_owner_agent(owner, data_dir)
        claims.make_claim(
            agent_id=owner,
            path=f"LOCK:{MERGE_LOCK_PATH}",
            intent=ClaimIntent.EDIT,
            ttl_s=_CONTROL_TTL_S,
            reason=reason or "merge lock enabled",
            force=True,
            data_dir=data_dir,
        )
        return
    _force_clear_resource(MERGE_LOCK_PATH, data_dir=data_dir)


def is_merges_locked(data_dir: Path | None = None) -> bool:
    return bool(claims.check(MERGE_LOCK_PATH, resource_type=ResourceType.LOCK, data_dir=data_dir))


def merge_lock_holders(data_dir: Path | None = None) -> list[Claim]:
    return claims.check(MERGE_LOCK_PATH, resource_type=ResourceType.LOCK, data_dir=data_dir)
