"""Tests for SQLite database layer."""

from __future__ import annotations

from pathlib import Path

import sqlite3

import pytest

from agentmesh import db
from agentmesh.models import Agent, AgentKind, AgentStatus, Claim, ClaimState, ClaimIntent, _now


def _create_legacy_db(data_dir: Path, with_claims_index: bool) -> None:
    """Create pre-resource-type schema directly on disk."""
    db_path = data_dir / "board.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE agents (
                agent_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL DEFAULT 'claude_code',
                display_name TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                pid INTEGER,
                tty TEXT,
                status TEXT NOT NULL DEFAULT 'idle',
                registered_at TEXT NOT NULL,
                last_heartbeat TEXT NOT NULL,
                meta TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE claims (
                claim_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL REFERENCES agents(agent_id),
                path TEXT NOT NULL,
                intent TEXT NOT NULL DEFAULT 'edit',
                state TEXT NOT NULL DEFAULT 'active',
                ttl_s INTEGER NOT NULL DEFAULT 1800,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                released_at TEXT,
                reason TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE messages (
                msg_id TEXT PRIMARY KEY,
                from_agent TEXT NOT NULL,
                to_agent TEXT,
                channel TEXT NOT NULL DEFAULT 'general',
                severity TEXT NOT NULL DEFAULT 'FYI',
                body TEXT NOT NULL DEFAULT '',
                read_by TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            );
            CREATE TABLE capsules (
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
                created_at TEXT NOT NULL
            );
            """
        )
        if with_claims_index:
            conn.execute(
                "CREATE INDEX idx_claims_active_path ON claims(path) WHERE state = 'active'"
            )
        conn.commit()
    finally:
        conn.close()


def test_init_db_creates_tables(tmp_data_dir: Path) -> None:
    conn = db.get_connection(tmp_data_dir)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    conn.close()
    names = [r["name"] for r in tables]
    assert "agents" in names
    assert "claims" in names
    assert "messages" in names
    assert "capsules" in names


def test_wal_mode(tmp_data_dir: Path) -> None:
    conn = db.get_connection(tmp_data_dir)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode == "wal"


def test_register_and_get_agent(tmp_data_dir: Path) -> None:
    now = _now()
    a = Agent(
        agent_id="test01", kind=AgentKind.CLAUDE_CODE, display_name="Test",
        cwd="/tmp", pid=1234, status=AgentStatus.IDLE,
        registered_at=now, last_heartbeat=now,
    )
    db.register_agent(a, tmp_data_dir)
    got = db.get_agent("test01", tmp_data_dir)
    assert got is not None
    assert got.agent_id == "test01"
    assert got.display_name == "Test"


def test_deregister_agent(tmp_data_dir: Path) -> None:
    a = Agent(agent_id="test02", cwd="/tmp")
    db.register_agent(a, tmp_data_dir)
    ok = db.deregister_agent("test02", tmp_data_dir)
    assert ok
    got = db.get_agent("test02", tmp_data_dir)
    assert got is not None
    assert got.status == AgentStatus.GONE


def test_list_agents_excludes_gone(tmp_data_dir: Path) -> None:
    db.register_agent(Agent(agent_id="a1", cwd="/tmp"), tmp_data_dir)
    db.register_agent(Agent(agent_id="a2", cwd="/tmp"), tmp_data_dir)
    db.deregister_agent("a2", tmp_data_dir)
    active = db.list_agents(tmp_data_dir)
    assert len(active) == 1
    assert active[0].agent_id == "a1"
    all_agents = db.list_agents(tmp_data_dir, include_gone=True)
    assert len(all_agents) == 2


def test_update_heartbeat(tmp_data_dir: Path) -> None:
    db.register_agent(Agent(agent_id="a3", cwd="/tmp"), tmp_data_dir)
    ok = db.update_heartbeat("a3", AgentStatus.BUSY, data_dir=tmp_data_dir)
    assert ok
    got = db.get_agent("a3", tmp_data_dir)
    assert got is not None
    assert got.status == AgentStatus.BUSY


def test_foreign_key_enforcement(tmp_data_dir: Path) -> None:
    """Creating a claim for a nonexistent agent should raise IntegrityError."""
    claim = Claim(
        claim_id="clm_orphan", agent_id="nonexistent", path="/tmp/x.py",
        intent=ClaimIntent.EDIT, state=ClaimState.ACTIVE,
        ttl_s=1800, created_at=_now(), expires_at=_now(),
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.create_claim(claim, tmp_data_dir)


def test_retry_on_busy_succeeds_after_transient() -> None:
    """Retry decorator should succeed when lock clears on second attempt."""
    call_count = 0

    @db._retry_on_busy
    def flaky_fn():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    result = flaky_fn()
    assert result == "ok"
    assert call_count == 2


def test_retry_on_busy_gives_up() -> None:
    """After max retries, the OperationalError should propagate."""
    call_count = 0

    @db._retry_on_busy
    def always_locked():
        nonlocal call_count
        call_count += 1
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        always_locked()
    assert call_count == db._BUSY_MAX_RETRIES + 1


def test_init_db_migrates_legacy_schema_without_index(tmp_path: Path) -> None:
    """Legacy DBs without idx_claims_active_path should still migrate cleanly."""
    data_dir = tmp_path / "legacy_no_idx"
    data_dir.mkdir()
    _create_legacy_db(data_dir, with_claims_index=False)

    db.init_db(data_dir)

    conn = sqlite3.connect(str(data_dir / "board.db"))
    try:
        claim_cols = [r[1] for r in conn.execute("PRAGMA table_info(claims)").fetchall()]
        capsule_cols = [r[1] for r in conn.execute("PRAGMA table_info(capsules)").fetchall()]
        idx_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_claims_active_path'"
        ).fetchone()
        assert "resource_type" in claim_cols
        assert "sbar" in capsule_cols
        assert idx_row is not None
        assert "resource_type" in (idx_row[0] or "").lower()
    finally:
        conn.close()


def test_migrated_claims_enforce_resource_type_check(tmp_path: Path) -> None:
    """Legacy DB migration should rebuild claims with resource_type CHECK constraint."""
    data_dir = tmp_path / "legacy_with_idx"
    data_dir.mkdir()
    _create_legacy_db(data_dir, with_claims_index=True)
    db.init_db(data_dir)
    db.register_agent(Agent(agent_id="a1", cwd="/tmp"), data_dir)

    conn = sqlite3.connect(str(data_dir / "board.db"))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO claims "
                "(claim_id, agent_id, path, resource_type, intent, state, ttl_s, "
                "created_at, expires_at, released_at, reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "clm_bad_resource",
                    "a1",
                    "/tmp/bad.py",
                    "bogus",
                    "edit",
                    "active",
                    30,
                    _now(),
                    _now(),
                    None,
                    "",
                ),
            )
    finally:
        conn.close()
