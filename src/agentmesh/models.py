"""Pydantic models for AgentMesh."""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class AgentKind(str, enum.Enum):
    CLAUDE_CODE = "claude_code"
    CODEX = "codex"
    CUSTOM = "custom"


class AgentStatus(str, enum.Enum):
    IDLE = "idle"
    BUSY = "busy"
    BLOCKED = "blocked"
    GONE = "gone"


class ResourceType(str, enum.Enum):
    FILE = "file"
    PORT = "port"
    LOCK = "lock"
    TEST_SUITE = "test_suite"
    TEMP_DIR = "temp_dir"


class ClaimIntent(str, enum.Enum):
    EDIT = "edit"
    READ = "read"
    TEST = "test"
    REVIEW = "review"


class ClaimState(str, enum.Enum):
    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"


class Severity(str, enum.Enum):
    FYI = "FYI"
    ATTN = "ATTN"
    BLOCKER = "BLOCKER"
    HANDOFF = "HANDOFF"


class EventKind(str, enum.Enum):
    REGISTER = "REGISTER"
    DEREGISTER = "DEREGISTER"
    HEARTBEAT = "HEARTBEAT"
    CLAIM = "CLAIM"
    RELEASE = "RELEASE"
    EXPIRE = "EXPIRE"
    MSG = "MSG"
    BUNDLE = "BUNDLE"
    STATUS_CHANGE = "STATUS_CHANGE"
    GC = "GC"
    SOFT_CONFLICT = "SOFT_CONFLICT"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Agent(BaseModel, frozen=True):
    agent_id: str
    kind: AgentKind = AgentKind.CLAUDE_CODE
    display_name: str = ""
    cwd: str = ""
    pid: int | None = None
    tty: str | None = None
    status: AgentStatus = AgentStatus.IDLE
    registered_at: str = Field(default_factory=_now)
    last_heartbeat: str = Field(default_factory=_now)
    meta: dict[str, Any] = Field(default_factory=dict)


class Claim(BaseModel, frozen=True):
    claim_id: str
    agent_id: str
    path: str
    resource_type: ResourceType = ResourceType.FILE
    intent: ClaimIntent = ClaimIntent.EDIT
    state: ClaimState = ClaimState.ACTIVE
    ttl_s: int = 1800
    created_at: str = Field(default_factory=_now)
    expires_at: str = ""
    released_at: str | None = None
    reason: str = ""


class Message(BaseModel, frozen=True):
    msg_id: str
    from_agent: str
    to_agent: str | None = None
    channel: str = "general"
    severity: Severity = Severity.FYI
    body: str = ""
    read_by: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now)


class Capsule(BaseModel, frozen=True):
    capsule_id: str
    agent_id: str
    task_desc: str = ""
    git_branch: str = ""
    git_sha: str = ""
    diff_stat: str = ""
    files_changed: list[str] = Field(default_factory=list)
    test_status: str = "unknown"
    test_summary: str = ""
    what_changed: str = ""
    what_remains: str = ""
    risks: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    sbar: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)


class Event(BaseModel, frozen=True):
    event_id: str
    seq: int
    ts: str = Field(default_factory=_now)
    kind: EventKind
    agent_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    prev_hash: str = ""
    event_hash: str = ""
