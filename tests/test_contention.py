"""Contention tests -- concurrent claim races, steal guards, and heavy weave append."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agentmesh import db
from agentmesh.claims import make_claim, normalize_path
from agentmesh.models import Agent, ResourceType
from agentmesh.waiters import steal_resource
from agentmesh.weaver import append_weave, verify_weave


def _register(agent_id: str, data_dir: Path) -> None:
    db.register_agent(Agent(agent_id=agent_id, cwd="/tmp"), data_dir)


# -- S1: same-file claim race --

def test_same_file_claim_race(tmp_data_dir: Path) -> None:
    """Two agents race to claim the same file via ThreadPoolExecutor.

    Exactly one must win, one must get conflicts.  No orphan active claims.
    """
    _register("racer_a", tmp_data_dir)
    _register("racer_b", tmp_data_dir)

    target = normalize_path("/tmp/contended.py")

    def _claim(agent_id: str) -> tuple[bool, list]:
        ok, _clm, conflicts = make_claim(agent_id, "/tmp/contended.py", data_dir=tmp_data_dir)
        return ok, conflicts

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_a = pool.submit(_claim, "racer_a")
        fut_b = pool.submit(_claim, "racer_b")
        result_a = fut_a.result()
        result_b = fut_b.result()

    wins = [r for r in [result_a, result_b] if r[0]]
    losses = [r for r in [result_a, result_b] if not r[0]]

    # Exactly one winner, one loser
    assert len(wins) == 1, f"Expected 1 winner, got {len(wins)}"
    assert len(losses) == 1, f"Expected 1 loser, got {len(losses)}"

    # Loser must have received conflict info
    loser_conflicts = losses[0][1]
    assert len(loser_conflicts) >= 1, "Loser should see at least 1 conflict"

    # No orphan active claims: exactly 1 active edit claim on that path
    all_active = db.list_claims(tmp_data_dir, active_only=True)
    active_on_target = [c for c in all_active if c.path == target]
    assert len(active_on_target) == 1, (
        f"Expected exactly 1 active claim on target, got {len(active_on_target)}"
    )


# -- S2: port/resource double-claim race --

def test_port_double_claim_race(tmp_data_dir: Path) -> None:
    """Two agents race for PORT:3000; exactly one winner.

    db.list_claims(active_only=True) for that port has exactly 1 entry.
    """
    _register("port_a", tmp_data_dir)
    _register("port_b", tmp_data_dir)

    def _claim(agent_id: str) -> bool:
        ok, _clm, _conflicts = make_claim(agent_id, "PORT:3000", data_dir=tmp_data_dir)
        return ok

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_a = pool.submit(_claim, "port_a")
        fut_b = pool.submit(_claim, "port_b")
        ok_a = fut_a.result()
        ok_b = fut_b.result()

    # Exactly one winner
    assert ok_a != ok_b, f"Expected exactly one winner: a={ok_a}, b={ok_b}"

    # Exactly 1 active claim for PORT:3000
    all_active = db.list_claims(tmp_data_dir, active_only=True)
    port_claims = [
        c for c in all_active
        if c.resource_type == ResourceType.PORT and c.path == "3000"
    ]
    assert len(port_claims) == 1, (
        f"Expected exactly 1 active PORT:3000 claim, got {len(port_claims)}"
    )


# -- S4: steal against fresh holder fails --

def test_steal_against_fresh_holder_fails(tmp_data_dir: Path) -> None:
    """Agent tries to steal while holder is active with fresh heartbeat.

    Must fail. Holder retains claim.
    """
    _register("holder", tmp_data_dir)
    _register("thief", tmp_data_dir)

    # Holder claims the file with a long TTL
    ok, _clm, _conflicts = make_claim(
        "holder", "/tmp/guarded.py", ttl_s=3600, data_dir=tmp_data_dir,
    )
    assert ok, "Holder should acquire claim"

    # Refresh holder heartbeat (make it current)
    db.update_heartbeat("holder", data_dir=tmp_data_dir)

    target = normalize_path("/tmp/guarded.py")

    # Thief attempts steal with a short stale threshold
    stolen, msg = steal_resource(
        "thief", target, reason="hostile takeover", stale_threshold_s=300,
        data_dir=tmp_data_dir,
    )
    assert not stolen, f"Steal should fail, but succeeded with msg: {msg}"
    assert "still active" in msg

    # Holder retains their active claim
    holder_claims = db.list_claims(tmp_data_dir, agent_id="holder", active_only=True)
    holder_on_target = [c for c in holder_claims if c.path == target]
    assert len(holder_on_target) == 1, "Holder must still own the claim"

    # Thief has no active claim on that path
    thief_claims = db.list_claims(tmp_data_dir, agent_id="thief", active_only=True)
    thief_on_target = [c for c in thief_claims if c.path == target]
    assert len(thief_on_target) == 0, "Thief must not hold any claim"


# -- S11: heavy concurrent weave append --

def test_heavy_concurrent_weave_append(tmp_data_dir: Path) -> None:
    """50 concurrent writers via ThreadPoolExecutor(max_workers=16).

    All must get unique monotonic sequence IDs, no gaps, verify_weave() passes.
    """
    n = 50

    def _append(i: int) -> int:
        evt = append_weave(capsule_id=f"heavy_{i}", data_dir=tmp_data_dir)
        return evt.sequence_id

    with ThreadPoolExecutor(max_workers=16) as pool:
        seqs = list(pool.map(_append, range(n)))

    # All sequence IDs must be unique
    assert len(set(seqs)) == n, (
        f"Expected {n} unique sequence IDs, got {len(set(seqs))}"
    )

    # Sorted sequence IDs must form a contiguous range 1..n (no gaps)
    assert sorted(seqs) == list(range(1, n + 1)), (
        f"Sequence IDs not contiguous 1..{n}: {sorted(seqs)}"
    )

    # Hash chain must verify cleanly
    valid, err = verify_weave(tmp_data_dir)
    assert valid, f"Weave verification failed: {err}"
