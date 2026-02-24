"""End-to-end multi-agent scenario test."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentmesh import db
from agentmesh.claims import make_claim, release, check
from agentmesh.capsules import build_capsule, get_capsule_bundle
from agentmesh.events import append_event, read_events, verify_chain, gc_events
from agentmesh.messages import post, inbox
from agentmesh.models import Agent, AgentStatus, ClaimIntent, EventKind, Severity


def _register(agent_id: str, data_dir: Path, name: str = "") -> None:
    db.register_agent(
        Agent(agent_id=agent_id, cwd="/tmp", display_name=name or agent_id),
        data_dir,
    )


def test_full_multi_agent_scenario(tmp_data_dir: Path) -> None:
    """Simulate two agents coordinating on a shared repo."""
    # 1. Register two agents
    _register("claude_alpha", tmp_data_dir, "Claude Alpha")
    _register("codex_beta", tmp_data_dir, "Codex Beta")

    agents = db.list_agents(tmp_data_dir)
    assert len(agents) == 2

    # 2. Alpha claims src/auth.py
    ok, clm, conflicts = make_claim(
        "claude_alpha", "/repo/src/auth.py",
        reason="Implementing login flow", data_dir=tmp_data_dir,
    )
    assert ok

    # 3. Beta tries to claim same file -- CONFLICT
    ok2, _, conflicts2 = make_claim(
        "codex_beta", "/repo/src/auth.py", data_dir=tmp_data_dir,
    )
    assert not ok2
    assert len(conflicts2) == 1
    assert conflicts2[0].agent_id == "claude_alpha"

    # 4. Beta claims different file -- OK
    ok3, _, _ = make_claim(
        "codex_beta", "/repo/src/models.py", data_dir=tmp_data_dir,
    )
    assert ok3

    # 5. Beta sends BLOCKER message to Alpha
    msg = post(
        "codex_beta", "Need auth.py types to proceed",
        to_agent="claude_alpha", severity=Severity.BLOCKER,
        data_dir=tmp_data_dir,
    )
    assert msg.severity == Severity.BLOCKER

    # 6. Alpha reads inbox
    alpha_msgs = inbox(agent_id="claude_alpha", data_dir=tmp_data_dir)
    assert len(alpha_msgs) >= 1
    assert any("auth.py" in m.body for m in alpha_msgs)

    # 7. Alpha releases auth.py
    count = release("claude_alpha", path="/repo/src/auth.py", data_dir=tmp_data_dir)
    assert count == 1

    # 8. Beta can now claim auth.py
    ok4, _, conflicts4 = make_claim(
        "codex_beta", "/repo/src/auth.py", data_dir=tmp_data_dir,
    )
    assert ok4
    assert conflicts4 == []

    # 9. Alpha emits context capsule
    cap = build_capsule("claude_alpha", task_desc="Auth module", data_dir=tmp_data_dir)
    assert cap.capsule_id.startswith("cap_")

    bundle = get_capsule_bundle(cap.capsule_id, data_dir=tmp_data_dir)
    assert bundle is not None
    assert bundle["task_desc"] == "Auth module"

    # 10. Verify event chain integrity
    valid, err = verify_chain(tmp_data_dir)
    assert valid, f"Chain broken: {err}"

    events = read_events(tmp_data_dir)
    assert len(events) >= 6  # register x2, claim x3+, release, msg, bundle

    # 11. Status check
    from agentmesh.status import render_status
    result = render_status(data_dir=tmp_data_dir, as_json=True)
    data = json.loads(result)
    assert len(data["agents"]) == 2
    assert len(data["claims"]) >= 1


def test_gc_events(tmp_data_dir: Path) -> None:
    """GC should remove old events and rechain hashes."""
    for i in range(5):
        append_event(EventKind.HEARTBEAT, agent_id="a1", data_dir=tmp_data_dir)

    # GC with 0 max_age should remove nothing (events are fresh)
    removed = gc_events(max_age_hours=0, data_dir=tmp_data_dir)
    # All events are "now" so 0h window means all events are older -- should remove all
    assert removed == 5

    # Verify chain is still valid after rewrite
    valid, err = verify_chain(tmp_data_dir)
    assert valid, err


def test_gc_preserves_recent(tmp_data_dir: Path) -> None:
    """GC with large window should keep recent events."""
    for i in range(3):
        append_event(EventKind.HEARTBEAT, agent_id="a1", data_dir=tmp_data_dir)

    removed = gc_events(max_age_hours=9999, data_dir=tmp_data_dir)
    assert removed == 0
    events = read_events(tmp_data_dir)
    assert len(events) == 3


def test_gc_keeps_event_ids_unique_for_future_appends(tmp_data_dir: Path) -> None:
    """After GC trims old head events, future appends should not duplicate event_id."""
    for i in range(20):
        append_event(EventKind.HEARTBEAT, agent_id="a1", payload={"i": i}, data_dir=tmp_data_dir)

    path = tmp_data_dir / "events.jsonl"
    lines = path.read_text().splitlines()
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=96)).isoformat()
    rewritten: list[str] = []
    for i, line in enumerate(lines):
        data = json.loads(line)
        if i < 9:
            data["ts"] = old_ts
        rewritten.append(json.dumps(data, separators=(",", ":")))
    path.write_text("\n".join(rewritten) + "\n")

    removed = gc_events(max_age_hours=72, data_dir=tmp_data_dir)
    assert removed == 9

    before_ids = [e.event_id for e in read_events(tmp_data_dir)]
    new_evt = append_event(EventKind.MSG, agent_id="a1", data_dir=tmp_data_dir)
    after_ids = [e.event_id for e in read_events(tmp_data_dir)]

    assert new_evt.event_id not in before_ids
    assert len(after_ids) == len(set(after_ids))


def test_deregister_releases_claims(tmp_data_dir: Path) -> None:
    """Deregistering marks agent gone; claims persist but are stale."""
    _register("a1", tmp_data_dir)
    make_claim("a1", "/tmp/f.py", data_dir=tmp_data_dir)
    db.deregister_agent("a1", tmp_data_dir)
    agent = db.get_agent("a1", tmp_data_dir)
    assert agent.status == AgentStatus.GONE
    # Claims still exist (GC cleans them later)
    all_claims = db.list_claims(tmp_data_dir, agent_id="a1")
    assert len(all_claims) == 1
