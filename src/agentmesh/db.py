"""SQLite WAL database layer for AgentMesh."""

from __future__ import annotations

import functools
import json
import os
import random
import sqlite3
import time
from pathlib import Path
from typing import Any

from .models import Agent, AgentStatus, Claim, ClaimIntent, ClaimState, Message, ResourceType, Severity, Capsule

_DEFAULT_DIR = Path.home() / ".agentmesh"

_BUSY_MAX_RETRIES = 3
_BUSY_BASE_DELAY_S = 0.1
_BUSY_JITTER_MAX_S = 0.1


def _retry_on_busy(fn):
    """Retry on SQLITE_BUSY with exponential backoff + jitter.

    Catches sqlite3.OperationalError containing 'locked' and retries
    up to _BUSY_MAX_RETRIES times with delays of 100ms, 200ms, 400ms
    plus random 0-100ms jitter.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        last_err = None
        for attempt in range(_BUSY_MAX_RETRIES + 1):
            try:
                return fn(*args, **kwargs)
            except sqlite3.OperationalError as e:
                if "locked" not in str(e).lower():
                    raise
                last_err = e
                if attempt < _BUSY_MAX_RETRIES:
                    delay = _BUSY_BASE_DELAY_S * (2 ** attempt) + random.uniform(0, _BUSY_JITTER_MAX_S)
                    time.sleep(delay)
        raise last_err  # type: ignore[misc]
    return wrapper

_SCHEMA = """\
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT 'claude_code',
    display_name TEXT NOT NULL DEFAULT '',
    cwd TEXT NOT NULL DEFAULT '',
    pid INTEGER,
    tty TEXT,
    status TEXT NOT NULL DEFAULT 'idle'
        CHECK(status IN ('idle','busy','blocked','gone')),
    registered_at TEXT NOT NULL,
    last_heartbeat TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(agent_id),
    path TEXT NOT NULL,
    resource_type TEXT NOT NULL DEFAULT 'file'
        CHECK(resource_type IN ('file','port','lock','test_suite','temp_dir')),
    intent TEXT NOT NULL DEFAULT 'edit'
        CHECK(intent IN ('edit','read','test','review')),
    state TEXT NOT NULL DEFAULT 'active'
        CHECK(state IN ('active','released','expired')),
    ttl_s INTEGER NOT NULL DEFAULT 1800,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    released_at TEXT,
    reason TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_claims_active_path
    ON claims(resource_type, path) WHERE state = 'active';

CREATE TABLE IF NOT EXISTS messages (
    msg_id TEXT PRIMARY KEY,
    from_agent TEXT NOT NULL,
    to_agent TEXT,
    channel TEXT NOT NULL DEFAULT 'general',
    severity TEXT NOT NULL DEFAULT 'FYI'
        CHECK(severity IN ('FYI','ATTN','BLOCKER','HANDOFF')),
    body TEXT NOT NULL DEFAULT '',
    read_by TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS capsules (
    capsule_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    task_desc TEXT NOT NULL DEFAULT '',
    git_branch TEXT NOT NULL DEFAULT '',
    git_sha TEXT NOT NULL DEFAULT '',
    diff_stat TEXT NOT NULL DEFAULT '',
    files_changed TEXT NOT NULL DEFAULT '[]',
    test_status TEXT NOT NULL DEFAULT 'unknown',
    test_summary TEXT NOT NULL DEFAULT '',
    what_changed TEXT NOT NULL DEFAULT '',
    what_remains TEXT NOT NULL DEFAULT '',
    risks TEXT NOT NULL DEFAULT '[]',
    next_actions TEXT NOT NULL DEFAULT '[]',
    sbar TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
"""


def _db_path(data_dir: Path | None = None) -> Path:
    d = data_dir or _DEFAULT_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d / "board.db"


def get_connection(data_dir: Path | None = None) -> sqlite3.Connection:
    """Get a new SQLite connection with WAL mode."""
    path = _db_path(data_dir)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(data_dir: Path | None = None) -> None:
    """Initialize schema if needed."""
    conn = get_connection(data_dir)
    try:
        conn.executescript(_SCHEMA)
    finally:
        conn.close()
    migrate_claims_add_resource_type(data_dir)
    migrate_capsules_add_sbar(data_dir)


def migrate_claims_add_resource_type(data_dir: Path | None = None) -> None:
    """Add resource_type column to existing claims table if missing."""
    conn = get_connection(data_dir)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(claims)").fetchall()]
        if "resource_type" not in cols:
            conn.execute(
                "ALTER TABLE claims ADD COLUMN resource_type TEXT NOT NULL DEFAULT 'file'"
            )
            conn.commit()
    finally:
        conn.close()


def migrate_capsules_add_sbar(data_dir: Path | None = None) -> None:
    """Add sbar column to existing capsules table if missing."""
    conn = get_connection(data_dir)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(capsules)").fetchall()]
        if "sbar" not in cols:
            conn.execute(
                "ALTER TABLE capsules ADD COLUMN sbar TEXT NOT NULL DEFAULT '{}'"
            )
            conn.commit()
    finally:
        conn.close()


# -- Agent CRUD --

def register_agent(agent: Agent, data_dir: Path | None = None) -> None:
    conn = get_connection(data_dir)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO agents "
            "(agent_id, kind, display_name, cwd, pid, tty, status, registered_at, last_heartbeat, meta) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (agent.agent_id, agent.kind.value, agent.display_name, agent.cwd,
             agent.pid, agent.tty, agent.status.value, agent.registered_at,
             agent.last_heartbeat, json.dumps(agent.meta)),
        )
        conn.commit()
    finally:
        conn.close()


def deregister_agent(agent_id: str, data_dir: Path | None = None) -> bool:
    conn = get_connection(data_dir)
    try:
        cur = conn.execute(
            "UPDATE agents SET status = 'gone' WHERE agent_id = ?", (agent_id,)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_agent(agent_id: str, data_dir: Path | None = None) -> Agent | None:
    conn = get_connection(data_dir)
    try:
        row = conn.execute(
            "SELECT * FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_agent(row)
    finally:
        conn.close()


def list_agents(data_dir: Path | None = None, include_gone: bool = False) -> list[Agent]:
    conn = get_connection(data_dir)
    try:
        if include_gone:
            rows = conn.execute("SELECT * FROM agents ORDER BY registered_at").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM agents WHERE status != 'gone' ORDER BY registered_at"
            ).fetchall()
        return [_row_to_agent(r) for r in rows]
    finally:
        conn.close()


def update_heartbeat(agent_id: str, status: AgentStatus | None = None,
                     ts: str | None = None, data_dir: Path | None = None) -> bool:
    from .models import _now
    conn = get_connection(data_dir)
    try:
        t = ts or _now()
        if status:
            cur = conn.execute(
                "UPDATE agents SET last_heartbeat = ?, status = ? WHERE agent_id = ?",
                (t, status.value, agent_id),
            )
        else:
            cur = conn.execute(
                "UPDATE agents SET last_heartbeat = ? WHERE agent_id = ?",
                (t, agent_id),
            )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# -- Claim CRUD --

def create_claim(claim: Claim, data_dir: Path | None = None) -> None:
    conn = get_connection(data_dir)
    try:
        conn.execute(
            "INSERT INTO claims "
            "(claim_id, agent_id, path, resource_type, intent, state, ttl_s, "
            "created_at, expires_at, released_at, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (claim.claim_id, claim.agent_id, claim.path, claim.resource_type.value,
             claim.intent.value, claim.state.value, claim.ttl_s, claim.created_at,
             claim.expires_at, claim.released_at, claim.reason),
        )
        conn.commit()
    finally:
        conn.close()


def check_collision(path: str, resource_type: ResourceType = ResourceType.FILE,
                    exclude_agent: str | None = None,
                    data_dir: Path | None = None) -> list[Claim]:
    """Return active edit claims on path+resource_type, optionally excluding an agent."""
    conn = get_connection(data_dir)
    try:
        if exclude_agent:
            rows = conn.execute(
                "SELECT * FROM claims WHERE path = ? AND resource_type = ? AND state = 'active' "
                "AND intent = 'edit' AND agent_id != ?",
                (path, resource_type.value, exclude_agent),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM claims WHERE path = ? AND resource_type = ? "
                "AND state = 'active' AND intent = 'edit'",
                (path, resource_type.value),
            ).fetchall()
        return [_row_to_claim(r) for r in rows]
    finally:
        conn.close()


@_retry_on_busy
def check_and_claim(claim: Claim, force: bool = False,
                    data_dir: Path | None = None) -> tuple[bool, list[Claim]]:
    """Atomic collision check + claim. Returns (success, conflicting_claims)."""
    conn = get_connection(data_dir)
    try:
        conn.execute("BEGIN IMMEDIATE")
        # Expire stale claims first
        from .models import _now
        now = _now()
        conn.execute(
            "UPDATE claims SET state = 'expired' WHERE state = 'active' AND expires_at < ?",
            (now,),
        )
        # Check for conflicts (only edit vs edit, same resource_type + path)
        conflicts = []
        if claim.intent == ClaimIntent.EDIT:
            rows = conn.execute(
                "SELECT * FROM claims WHERE path = ? AND resource_type = ? AND state = 'active' "
                "AND intent = 'edit' AND agent_id != ?",
                (claim.path, claim.resource_type.value, claim.agent_id),
            ).fetchall()
            conflicts = [_row_to_claim(r) for r in rows]

        if conflicts and not force:
            conn.rollback()
            return False, conflicts

        # Force: expire conflicting claims by other agents
        if conflicts and force:
            conn.execute(
                "UPDATE claims SET state = 'expired' "
                "WHERE path = ? AND resource_type = ? AND state = 'active' "
                "AND intent = 'edit' AND agent_id != ?",
                (claim.path, claim.resource_type.value, claim.agent_id),
            )

        # Release any existing active claim by this agent on same path + resource_type
        conn.execute(
            "UPDATE claims SET state = 'released', released_at = ? "
            "WHERE agent_id = ? AND path = ? AND resource_type = ? AND state = 'active'",
            (now, claim.agent_id, claim.path, claim.resource_type.value),
        )
        # Insert new claim
        conn.execute(
            "INSERT INTO claims "
            "(claim_id, agent_id, path, resource_type, intent, state, ttl_s, "
            "created_at, expires_at, released_at, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (claim.claim_id, claim.agent_id, claim.path, claim.resource_type.value,
             claim.intent.value, claim.state.value, claim.ttl_s, claim.created_at,
             claim.expires_at, claim.released_at, claim.reason),
        )
        conn.commit()
        return True, conflicts
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@_retry_on_busy
def release_claim(agent_id: str, path: str | None = None,
                  resource_type: ResourceType = ResourceType.FILE,
                  release_all: bool = False,
                  data_dir: Path | None = None) -> int:
    from .models import _now
    conn = get_connection(data_dir)
    try:
        now = _now()
        if release_all:
            cur = conn.execute(
                "UPDATE claims SET state = 'released', released_at = ? "
                "WHERE agent_id = ? AND state = 'active'",
                (now, agent_id),
            )
        elif path:
            cur = conn.execute(
                "UPDATE claims SET state = 'released', released_at = ? "
                "WHERE agent_id = ? AND path = ? AND resource_type = ? AND state = 'active'",
                (now, agent_id, path, resource_type.value),
            )
        else:
            return 0
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def list_claims(data_dir: Path | None = None, agent_id: str | None = None,
                active_only: bool = True) -> list[Claim]:
    conn = get_connection(data_dir)
    try:
        q = "SELECT * FROM claims"
        params: list[Any] = []
        conditions = []
        if active_only:
            conditions.append("state = 'active'")
        if agent_id:
            conditions.append("agent_id = ?")
            params.append(agent_id)
        if conditions:
            q += " WHERE " + " AND ".join(conditions)
        q += " ORDER BY created_at"
        rows = conn.execute(q, params).fetchall()
        return [_row_to_claim(r) for r in rows]
    finally:
        conn.close()


def expire_stale_claims(data_dir: Path | None = None) -> int:
    from .models import _now
    conn = get_connection(data_dir)
    try:
        now = _now()
        cur = conn.execute(
            "UPDATE claims SET state = 'expired' WHERE state = 'active' AND expires_at < ?",
            (now,),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# -- Message CRUD --

@_retry_on_busy
def post_message(msg: Message, data_dir: Path | None = None) -> None:
    conn = get_connection(data_dir)
    try:
        conn.execute(
            "INSERT INTO messages "
            "(msg_id, from_agent, to_agent, channel, severity, body, read_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (msg.msg_id, msg.from_agent, msg.to_agent, msg.channel,
             msg.severity.value, msg.body, json.dumps(msg.read_by), msg.created_at),
        )
        conn.commit()
    finally:
        conn.close()


def list_messages(data_dir: Path | None = None, channel: str | None = None,
                  severity: Severity | None = None, to_agent: str | None = None,
                  unread_by: str | None = None, limit: int = 50) -> list[Message]:
    conn = get_connection(data_dir)
    try:
        q = "SELECT * FROM messages"
        params: list[Any] = []
        conditions = []
        if channel:
            conditions.append("channel = ?")
            params.append(channel)
        if severity:
            conditions.append("severity = ?")
            params.append(severity.value)
        if to_agent:
            conditions.append("(to_agent = ? OR to_agent IS NULL)")
            params.append(to_agent)
        if conditions:
            q += " WHERE " + " AND ".join(conditions)
        q += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(q, params).fetchall()
        msgs = [_row_to_message(r) for r in rows]
        if unread_by:
            msgs = [m for m in msgs if unread_by not in m.read_by]
        return msgs
    finally:
        conn.close()


def mark_read(msg_id: str, agent_id: str, data_dir: Path | None = None) -> None:
    conn = get_connection(data_dir)
    try:
        row = conn.execute("SELECT read_by FROM messages WHERE msg_id = ?", (msg_id,)).fetchone()
        if row:
            readers = json.loads(row["read_by"])
            if agent_id not in readers:
                readers.append(agent_id)
                conn.execute(
                    "UPDATE messages SET read_by = ? WHERE msg_id = ?",
                    (json.dumps(readers), msg_id),
                )
                conn.commit()
    finally:
        conn.close()


# -- Capsule CRUD --

@_retry_on_busy
def save_capsule(capsule: Capsule, data_dir: Path | None = None) -> None:
    conn = get_connection(data_dir)
    try:
        conn.execute(
            "INSERT INTO capsules "
            "(capsule_id, agent_id, task_desc, git_branch, git_sha, diff_stat, "
            "files_changed, test_status, test_summary, what_changed, what_remains, "
            "risks, next_actions, sbar, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (capsule.capsule_id, capsule.agent_id, capsule.task_desc,
             capsule.git_branch, capsule.git_sha, capsule.diff_stat,
             json.dumps(capsule.files_changed), capsule.test_status, capsule.test_summary,
             capsule.what_changed, capsule.what_remains,
             json.dumps(capsule.risks), json.dumps(capsule.next_actions),
             json.dumps(capsule.sbar), capsule.created_at),
        )
        conn.commit()
    finally:
        conn.close()


def get_capsule(capsule_id: str, data_dir: Path | None = None) -> Capsule | None:
    conn = get_connection(data_dir)
    try:
        row = conn.execute(
            "SELECT * FROM capsules WHERE capsule_id = ?", (capsule_id,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_capsule(row)
    finally:
        conn.close()


def list_capsules(data_dir: Path | None = None, agent_id: str | None = None,
                  limit: int = 20) -> list[Capsule]:
    conn = get_connection(data_dir)
    try:
        if agent_id:
            rows = conn.execute(
                "SELECT * FROM capsules WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?",
                (agent_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM capsules ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_capsule(r) for r in rows]
    finally:
        conn.close()


# -- GC --

def gc_old_data(max_age_hours: int = 72, data_dir: Path | None = None) -> dict[str, int]:
    """Remove old released/expired claims, gone agents, old messages."""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
    conn = get_connection(data_dir)
    try:
        c1 = conn.execute(
            "DELETE FROM claims WHERE state IN ('released','expired') AND created_at < ?",
            (cutoff,),
        ).rowcount
        c2 = conn.execute(
            "DELETE FROM agents WHERE status = 'gone' AND last_heartbeat < ?",
            (cutoff,),
        ).rowcount
        c3 = conn.execute(
            "DELETE FROM messages WHERE created_at < ?", (cutoff,),
        ).rowcount
        conn.commit()
        return {"claims": c1, "agents": c2, "messages": c3}
    finally:
        conn.close()


# -- Row converters --

def _row_to_agent(row: sqlite3.Row) -> Agent:
    return Agent(
        agent_id=row["agent_id"], kind=row["kind"], display_name=row["display_name"],
        cwd=row["cwd"], pid=row["pid"], tty=row["tty"], status=row["status"],
        registered_at=row["registered_at"], last_heartbeat=row["last_heartbeat"],
        meta=json.loads(row["meta"]),
    )


def _row_to_claim(row: sqlite3.Row) -> Claim:
    return Claim(
        claim_id=row["claim_id"], agent_id=row["agent_id"], path=row["path"],
        resource_type=row["resource_type"], intent=row["intent"],
        state=row["state"], ttl_s=row["ttl_s"],
        created_at=row["created_at"], expires_at=row["expires_at"],
        released_at=row["released_at"], reason=row["reason"],
    )


def _row_to_message(row: sqlite3.Row) -> Message:
    return Message(
        msg_id=row["msg_id"], from_agent=row["from_agent"], to_agent=row["to_agent"],
        channel=row["channel"], severity=row["severity"], body=row["body"],
        read_by=json.loads(row["read_by"]), created_at=row["created_at"],
    )


def _row_to_capsule(row: sqlite3.Row) -> Capsule:
    return Capsule(
        capsule_id=row["capsule_id"], agent_id=row["agent_id"],
        task_desc=row["task_desc"], git_branch=row["git_branch"],
        git_sha=row["git_sha"], diff_stat=row["diff_stat"],
        files_changed=json.loads(row["files_changed"]),
        test_status=row["test_status"], test_summary=row["test_summary"],
        what_changed=row["what_changed"], what_remains=row["what_remains"],
        risks=json.loads(row["risks"]), next_actions=json.loads(row["next_actions"]),
        sbar=json.loads(row["sbar"]),
        created_at=row["created_at"],
    )
