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


class TaskState(str, enum.Enum):
    PLANNED = "planned"
    ASSIGNED = "assigned"
    RUNNING = "running"
    PR_OPEN = "pr_open"
    CI_PASS = "ci_pass"
    REVIEW_PASS = "review_pass"
    MERGED = "merged"
    ABORTED = "aborted"


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
    EPISODE_START = "EPISODE_START"
    EPISODE_END = "EPISODE_END"
    WAIT = "WAIT"
    STEAL = "STEAL"
    COMMIT = "COMMIT"
    TASK_TRANSITION = "TASK_TRANSITION"
    WORKER_SPAWN = "WORKER_SPAWN"
    WORKER_DONE = "WORKER_DONE"
    ADAPTER_LOAD = "ADAPTER_LOAD"
    ORCH_FREEZE = "ORCH_FREEZE"
    ORCH_LOCK_MERGES = "ORCH_LOCK_MERGES"
    ORCH_ABORT_ALL = "ORCH_ABORT_ALL"
    ORCH_LEASE_RENEW = "ORCH_LEASE_RENEW"
    COST_EXCEEDED = "COST_EXCEEDED"
    TEST_MISMATCH = "TEST_MISMATCH"
    ASSAY_RECEIPT = "ASSAY_RECEIPT"


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
    episode_id: str = ""
    priority: int = 5
    effective_priority: int = 5


class Message(BaseModel, frozen=True):
    msg_id: str
    from_agent: str
    to_agent: str | None = None
    channel: str = "general"
    severity: Severity = Severity.FYI
    body: str = ""
    read_by: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now)
    episode_id: str = ""


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
    episode_id: str = ""


class Episode(BaseModel, frozen=True):
    episode_id: str
    title: str = ""
    started_at: str = Field(default_factory=_now)
    ended_at: str = ""
    parent_episode_id: str = ""


class WeaveEvent(BaseModel, frozen=True):
    event_id: str
    episode_id: str = ""
    prev_hash: str = ""
    capsule_id: str = ""
    git_commit_sha: str = ""
    git_patch_hash: str = ""
    affected_symbols: list[str] = Field(default_factory=list)
    trace_id: str = ""
    parent_event_id: str = ""
    event_hash: str = ""
    created_at: str = Field(default_factory=_now)


class Waiter(BaseModel, frozen=True):
    waiter_id: str
    resource_path: str
    resource_type: ResourceType = ResourceType.FILE
    waiter_agent_id: str = ""
    episode_id: str = ""
    priority: int = 5
    reason: str = ""
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


class Task(BaseModel, frozen=True):
    task_id: str
    title: str = ""
    description: str = ""
    state: TaskState = TaskState.PLANNED
    assigned_agent_id: str = ""
    episode_id: str = ""
    branch: str = ""
    pr_url: str = ""
    parent_task_id: str = ""
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    meta: dict[str, Any] = Field(default_factory=dict)


class Attempt(BaseModel, frozen=True):
    attempt_id: str
    task_id: str
    agent_id: str
    attempt_number: int = 1
    started_at: str = Field(default_factory=_now)
    ended_at: str = ""
    outcome: str = ""  # success, failure, aborted
    error_summary: str = ""
