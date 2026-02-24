"""Tests for claim collision detection -- 18 scenarios."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentmesh import db
from agentmesh.claims import make_claim, release, check, normalize_path, parse_resource_string
from agentmesh.models import Agent, ClaimIntent, ClaimState, ResourceType


def _register(agent_id: str, data_dir: Path) -> None:
    db.register_agent(Agent(agent_id=agent_id, cwd="/tmp"), data_dir)


def test_single_claim_succeeds(tmp_data_dir: Path) -> None:
    _register("a1", tmp_data_dir)
    ok, clm, conflicts = make_claim("a1", "/tmp/foo.py", data_dir=tmp_data_dir)
    assert ok
    assert clm.path == normalize_path("/tmp/foo.py")
    assert conflicts == []


def test_same_agent_reclaim(tmp_data_dir: Path) -> None:
    """Same agent reclaiming same file should succeed (releases old claim)."""
    _register("a1", tmp_data_dir)
    ok1, _, _ = make_claim("a1", "/tmp/foo.py", data_dir=tmp_data_dir)
    ok2, _, conflicts = make_claim("a1", "/tmp/foo.py", data_dir=tmp_data_dir)
    assert ok1 and ok2
    assert conflicts == []


def test_different_agent_edit_conflict(tmp_data_dir: Path) -> None:
    """Two agents claiming same file for edit = conflict."""
    _register("a1", tmp_data_dir)
    _register("a2", tmp_data_dir)
    ok1, _, _ = make_claim("a1", "/tmp/foo.py", data_dir=tmp_data_dir)
    ok2, _, conflicts = make_claim("a2", "/tmp/foo.py", data_dir=tmp_data_dir)
    assert ok1
    assert not ok2
    assert len(conflicts) == 1
    assert conflicts[0].agent_id == "a1"


def test_read_does_not_block_edit(tmp_data_dir: Path) -> None:
    """Read claims don't conflict with edit claims."""
    _register("a1", tmp_data_dir)
    _register("a2", tmp_data_dir)
    ok1, _, _ = make_claim("a1", "/tmp/foo.py", intent=ClaimIntent.READ, data_dir=tmp_data_dir)
    ok2, _, conflicts = make_claim("a2", "/tmp/foo.py", intent=ClaimIntent.EDIT, data_dir=tmp_data_dir)
    assert ok1 and ok2
    assert conflicts == []


def test_edit_does_not_block_read(tmp_data_dir: Path) -> None:
    """Edit claims don't block reads."""
    _register("a1", tmp_data_dir)
    _register("a2", tmp_data_dir)
    ok1, _, _ = make_claim("a1", "/tmp/foo.py", intent=ClaimIntent.EDIT, data_dir=tmp_data_dir)
    ok2, _, conflicts = make_claim("a2", "/tmp/foo.py", intent=ClaimIntent.READ, data_dir=tmp_data_dir)
    assert ok1 and ok2


def test_force_overrides_conflict(tmp_data_dir: Path) -> None:
    _register("a1", tmp_data_dir)
    _register("a2", tmp_data_dir)
    make_claim("a1", "/tmp/foo.py", data_dir=tmp_data_dir)
    ok, _, conflicts = make_claim("a2", "/tmp/foo.py", force=True, data_dir=tmp_data_dir)
    assert ok
    assert len(conflicts) == 1  # conflict existed but was overridden


def test_force_expires_prior_owners_claim(tmp_data_dir: Path) -> None:
    """After force-claim, only the forcing agent has an active claim."""
    _register("a1", tmp_data_dir)
    _register("a2", tmp_data_dir)
    make_claim("a1", "/tmp/foo.py", data_dir=tmp_data_dir)
    make_claim("a2", "/tmp/foo.py", force=True, data_dir=tmp_data_dir)
    # a1 should have zero active claims on this path
    a1_claims = db.list_claims(tmp_data_dir, agent_id="a1", active_only=True)
    a1_on_foo = [c for c in a1_claims if c.path == normalize_path("/tmp/foo.py")]
    assert len(a1_on_foo) == 0
    # a2 should be sole owner
    a2_claims = db.list_claims(tmp_data_dir, agent_id="a2", active_only=True)
    a2_on_foo = [c for c in a2_claims if c.path == normalize_path("/tmp/foo.py")]
    assert len(a2_on_foo) == 1


def test_release_clears_conflict(tmp_data_dir: Path) -> None:
    _register("a1", tmp_data_dir)
    _register("a2", tmp_data_dir)
    make_claim("a1", "/tmp/foo.py", data_dir=tmp_data_dir)
    release("a1", path="/tmp/foo.py", data_dir=tmp_data_dir)
    ok, _, conflicts = make_claim("a2", "/tmp/foo.py", data_dir=tmp_data_dir)
    assert ok
    assert conflicts == []


def test_release_all(tmp_data_dir: Path) -> None:
    _register("a1", tmp_data_dir)
    make_claim("a1", "/tmp/foo.py", data_dir=tmp_data_dir)
    make_claim("a1", "/tmp/bar.py", data_dir=tmp_data_dir)
    count = release("a1", release_all=True, data_dir=tmp_data_dir)
    assert count == 2


def test_expired_claim_no_conflict(tmp_data_dir: Path) -> None:
    """Expired claims should not cause conflicts."""
    _register("a1", tmp_data_dir)
    _register("a2", tmp_data_dir)
    # Create claim with 0-second TTL (already expired)
    ok1, _, _ = make_claim("a1", "/tmp/foo.py", ttl_s=0, data_dir=tmp_data_dir)
    assert ok1
    # Second agent should succeed because first claim is expired
    ok2, _, conflicts = make_claim("a2", "/tmp/foo.py", data_dir=tmp_data_dir)
    assert ok2
    assert conflicts == []


def test_check_finds_conflict(tmp_data_dir: Path) -> None:
    _register("a1", tmp_data_dir)
    make_claim("a1", "/tmp/foo.py", data_dir=tmp_data_dir)
    conflicts = check("/tmp/foo.py", data_dir=tmp_data_dir)
    assert len(conflicts) == 1


def test_check_excludes_self(tmp_data_dir: Path) -> None:
    _register("a1", tmp_data_dir)
    make_claim("a1", "/tmp/foo.py", data_dir=tmp_data_dir)
    conflicts = check("/tmp/foo.py", exclude_agent="a1", data_dir=tmp_data_dir)
    assert len(conflicts) == 0


def test_different_files_no_conflict(tmp_data_dir: Path) -> None:
    _register("a1", tmp_data_dir)
    _register("a2", tmp_data_dir)
    ok1, _, _ = make_claim("a1", "/tmp/foo.py", data_dir=tmp_data_dir)
    ok2, _, conflicts = make_claim("a2", "/tmp/bar.py", data_dir=tmp_data_dir)
    assert ok1 and ok2
    assert conflicts == []


# -- Resource type tests --

def test_port_claim(tmp_data_dir: Path) -> None:
    """Claiming a port resource should succeed and parse correctly."""
    _register("a1", tmp_data_dir)
    ok, clm, conflicts = make_claim("a1", "PORT:3000", data_dir=tmp_data_dir)
    assert ok
    assert clm.resource_type == ResourceType.PORT
    assert clm.path == "3000"
    assert conflicts == []


def test_port_collision(tmp_data_dir: Path) -> None:
    """Two agents claiming same port = conflict."""
    _register("a1", tmp_data_dir)
    _register("a2", tmp_data_dir)
    ok1, _, _ = make_claim("a1", "PORT:3000", data_dir=tmp_data_dir)
    ok2, _, conflicts = make_claim("a2", "PORT:3000", data_dir=tmp_data_dir)
    assert ok1
    assert not ok2
    assert len(conflicts) == 1


def test_cross_type_no_conflict(tmp_data_dir: Path) -> None:
    """PORT:3000 and FILE:/path/3000 should never conflict."""
    _register("a1", tmp_data_dir)
    _register("a2", tmp_data_dir)
    ok1, _, _ = make_claim("a1", "PORT:3000", data_dir=tmp_data_dir)
    ok2, _, conflicts = make_claim(
        "a2", "/tmp/3000", resource_type=ResourceType.FILE, data_dir=tmp_data_dir,
    )
    assert ok1 and ok2
    assert conflicts == []


def test_lock_claim(tmp_data_dir: Path) -> None:
    """Lock resources parse and claim correctly."""
    _register("a1", tmp_data_dir)
    ok, clm, _ = make_claim("a1", "LOCK:npm", data_dir=tmp_data_dir)
    assert ok
    assert clm.resource_type == ResourceType.LOCK
    assert clm.path == "npm"


def test_test_suite_claim(tmp_data_dir: Path) -> None:
    """TEST_SUITE resources parse and claim correctly."""
    _register("a1", tmp_data_dir)
    ok, clm, _ = make_claim("a1", "TEST_SUITE:integration", data_dir=tmp_data_dir)
    assert ok
    assert clm.resource_type == ResourceType.TEST_SUITE
    assert clm.path == "integration"


def test_backward_compat_bare_path(tmp_data_dir: Path) -> None:
    """Bare paths (no prefix) should still work as FILE claims."""
    _register("a1", tmp_data_dir)
    ok, clm, _ = make_claim("a1", "/tmp/foo.py", data_dir=tmp_data_dir)
    assert ok
    assert clm.resource_type == ResourceType.FILE
    assert clm.path == normalize_path("/tmp/foo.py")
