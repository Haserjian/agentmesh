"""Tests for JSONL event log."""

from __future__ import annotations

from pathlib import Path

from agentmesh.events import append_event, read_events, verify_chain, _GENESIS_HASH
from agentmesh.models import EventKind


def test_append_first_event(tmp_data_dir: Path) -> None:
    evt = append_event(EventKind.REGISTER, agent_id="a1", data_dir=tmp_data_dir)
    assert evt.seq == 1
    assert evt.prev_hash == _GENESIS_HASH
    assert evt.event_hash.startswith("sha256:")


def test_append_chain(tmp_data_dir: Path) -> None:
    e1 = append_event(EventKind.REGISTER, agent_id="a1", data_dir=tmp_data_dir)
    e2 = append_event(EventKind.HEARTBEAT, agent_id="a1", data_dir=tmp_data_dir)
    assert e2.seq == 2
    assert e2.prev_hash == e1.event_hash


def test_read_events(tmp_data_dir: Path) -> None:
    append_event(EventKind.REGISTER, agent_id="a1", data_dir=tmp_data_dir)
    append_event(EventKind.HEARTBEAT, agent_id="a1", data_dir=tmp_data_dir)
    append_event(EventKind.CLAIM, agent_id="a1", payload={"path": "foo.py"}, data_dir=tmp_data_dir)
    evts = read_events(tmp_data_dir)
    assert len(evts) == 3
    assert evts[0].kind == EventKind.REGISTER
    # since_seq filter
    evts2 = read_events(tmp_data_dir, since_seq=1)
    assert len(evts2) == 2


def test_verify_chain_valid(tmp_data_dir: Path) -> None:
    append_event(EventKind.REGISTER, agent_id="a1", data_dir=tmp_data_dir)
    append_event(EventKind.HEARTBEAT, agent_id="a1", data_dir=tmp_data_dir)
    valid, err = verify_chain(tmp_data_dir)
    assert valid
    assert err == ""


def test_verify_chain_tampered(tmp_data_dir: Path) -> None:
    append_event(EventKind.REGISTER, agent_id="a1", data_dir=tmp_data_dir)
    append_event(EventKind.HEARTBEAT, agent_id="a1", data_dir=tmp_data_dir)

    # Tamper with event log
    path = tmp_data_dir / "events.jsonl"
    import json
    lines = path.read_text().strip().split("\n")
    data = json.loads(lines[0])
    data["agent_id"] = "TAMPERED"
    lines[0] = json.dumps(data, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n")

    valid, err = verify_chain(tmp_data_dir)
    assert not valid
    assert "Hash mismatch" in err
