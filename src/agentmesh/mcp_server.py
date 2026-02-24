"""AgentMesh MCP server -- multi-agent coordination tools over Model Context Protocol."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

from . import claims, db, episodes, events, status
from .models import AgentKind, AgentStatus, Agent, ClaimIntent, EventKind, _now

mcp = FastMCP("agentmesh")


def _data_dir() -> Path | None:
    env = os.environ.get("AGENTMESH_DATA_DIR")
    return Path(env) if env else None


def _agent_id() -> str:
    env = os.environ.get("AGENTMESH_AGENT_ID")
    if env:
        return env
    return "mcp_agent"


def _ensure_agent(agent_id: str) -> None:
    """Register agent if it doesn't exist yet."""
    dd = _data_dir()
    if db.get_agent(agent_id, dd) is not None:
        return
    now = _now()
    a = Agent(
        agent_id=agent_id,
        kind=AgentKind.CLAUDE_CODE,
        display_name=agent_id,
        cwd=os.getcwd(),
        pid=os.getpid(),
        tty="",
        status=AgentStatus.IDLE,
        registered_at=now,
        last_heartbeat=now,
    )
    db.register_agent(a, dd)


def _init() -> None:
    db.init_db(_data_dir())


@mcp.tool()
def mesh_claim(
    resource: str,
    agent_id: Optional[str] = None,
    ttl_seconds: int = 1800,
    intent: str = "edit",
    reason: str = "",
    force: bool = False,
) -> dict:
    """Claim exclusive access to a resource before editing.

    Args:
        resource: Resource to claim. File paths (src/main.py), ports (PORT:3000), locks (LOCK:npm), etc.
        agent_id: Agent making the claim. Auto-detected if omitted.
        ttl_seconds: Seconds until the claim auto-expires.
        intent: Claim intent: edit, read, test, or review.
        reason: Why this resource is being claimed.
        force: Override existing claims from other agents.
    """
    _init()
    aid = agent_id or _agent_id()
    _ensure_agent(aid)
    claim_intent = ClaimIntent(intent)
    ok, clm, conflicts = claims.make_claim(
        aid, resource, intent=claim_intent, ttl_s=ttl_seconds,
        reason=reason, force=force, data_dir=_data_dir(),
    )
    if ok:
        return {
            "ok": True,
            "claim_id": clm.claim_id,
            "resource": clm.path,
            "resource_type": clm.resource_type.value,
            "agent_id": aid,
            "expires_at": clm.expires_at,
        }
    return {
        "ok": False,
        "resource": resource,
        "conflicts": [
            {"agent_id": c.agent_id, "path": c.path, "expires_at": c.expires_at}
            for c in conflicts
        ],
    }


@mcp.tool()
def mesh_release(
    resource: Optional[str] = None,
    agent_id: Optional[str] = None,
    release_all: bool = False,
) -> dict:
    """Release claimed resources.

    Args:
        resource: Specific resource to release. Omit if using release_all.
        agent_id: Agent releasing claims. Auto-detected if omitted.
        release_all: Release all claims held by this agent.
    """
    _init()
    aid = agent_id or _agent_id()
    if release_all:
        count = claims.release(aid, release_all=True, data_dir=_data_dir())
    elif resource:
        count = claims.release(aid, path=resource, data_dir=_data_dir())
    else:
        return {"ok": False, "error": "Specify resource or set release_all=true"}
    return {"ok": True, "released_count": count, "agent_id": aid}


@mcp.tool()
def mesh_check(
    resource: str,
    agent_id: Optional[str] = None,
) -> dict:
    """Check if a resource is claimed by another agent.

    Args:
        resource: Resource path to check (e.g. src/main.py, PORT:3000).
        agent_id: Exclude this agent from conflict check (your own claims won't conflict).
    """
    _init()
    conflicts = claims.check(resource, exclude_agent=agent_id, data_dir=_data_dir())
    if conflicts:
        return {
            "claimed": True,
            "resource": resource,
            "holders": [
                {"agent_id": c.agent_id, "intent": c.intent.value, "expires_at": c.expires_at}
                for c in conflicts
            ],
        }
    return {"claimed": False, "resource": resource}


@mcp.tool()
def mesh_status(
    agent_id: Optional[str] = None,
) -> dict:
    """Show current mesh status: active agents, claims, and episodes.

    Args:
        agent_id: Filter to a specific agent. Omit for full dashboard.
    """
    _init()
    dd = _data_dir()
    agents_list = db.list_agents(dd)
    claims_list = db.list_claims(dd, agent_id=agent_id, active_only=True)
    ep_id = episodes.get_current_episode(dd)
    return {
        "agents": [
            {"agent_id": a.agent_id, "status": a.status.value, "last_heartbeat": a.last_heartbeat}
            for a in agents_list
        ],
        "claims": [
            {"claim_id": c.claim_id, "agent_id": c.agent_id, "path": c.path,
             "resource_type": c.resource_type.value, "expires_at": c.expires_at}
            for c in claims_list
        ],
        "current_episode": ep_id or None,
    }


@mcp.tool()
def mesh_episode_start(
    title: str = "",
    agent_id: Optional[str] = None,
) -> dict:
    """Start a new coordination episode. Episodes group claims, commits, and capsules.

    Args:
        title: Human-readable title for this work session.
        agent_id: Agent starting the episode. Auto-detected if omitted.
    """
    _init()
    aid = agent_id or _agent_id()
    _ensure_agent(aid)
    existing = episodes.get_current_episode(_data_dir())
    if existing:
        return {"ok": True, "episode_id": existing, "reused": True}
    ep_id = episodes.start_episode(title=title, data_dir=_data_dir())
    events.append_event(
        EventKind.EPISODE_START,
        agent_id=aid,
        payload={"episode_id": ep_id, "title": title},
        data_dir=_data_dir(),
    )
    return {"ok": True, "episode_id": ep_id, "reused": False}


@mcp.tool()
def mesh_episode_end(
    agent_id: Optional[str] = None,
) -> dict:
    """End the current coordination episode.

    Args:
        agent_id: Agent ending the episode. Auto-detected if omitted.
    """
    _init()
    aid = agent_id or _agent_id()
    ep_id = episodes.end_episode(_data_dir())
    if not ep_id:
        return {"ok": False, "error": "No active episode"}
    events.append_event(
        EventKind.EPISODE_END,
        agent_id=aid,
        payload={"episode_id": ep_id},
        data_dir=_data_dir(),
    )
    return {"ok": True, "episode_id": ep_id}


def main():
    """Run the MCP server over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
