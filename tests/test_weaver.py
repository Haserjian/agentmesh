"""Tests for provenance weaver."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from typer.testing import CliRunner

from agentmesh import db
from agentmesh import events
from agentmesh.cli import app
from agentmesh.models import EventKind
from agentmesh.weaver import append_weave, verify_weave, trace_file, export_weave_md
from agentmesh.episodes import start_episode

runner = CliRunner()


def test_append_weave(tmp_data_dir: Path) -> None:
    evt = append_weave(
        capsule_id="cap_abc", git_commit_sha="abc1234",
        affected_symbols=["src/auth.py:login"],
        episode_id="", data_dir=tmp_data_dir,
    )
    assert evt.event_id.startswith("weave_")
    assert evt.capsule_id == "cap_abc"
    assert evt.git_commit_sha == "abc1234"
    assert evt.event_hash.startswith("sha256:")


def test_chain_valid(tmp_data_dir: Path) -> None:
    append_weave(capsule_id="c1", episode_id="", data_dir=tmp_data_dir)
    append_weave(capsule_id="c2", episode_id="", data_dir=tmp_data_dir)
    valid, err = verify_weave(tmp_data_dir)
    assert valid
    assert err == ""


def test_chain_links(tmp_data_dir: Path) -> None:
    e1 = append_weave(capsule_id="c1", episode_id="", data_dir=tmp_data_dir)
    e2 = append_weave(capsule_id="c2", episode_id="", data_dir=tmp_data_dir)
    assert e2.prev_hash == e1.event_hash


def test_auto_tag_episode(tmp_data_dir: Path) -> None:
    ep_id = start_episode(title="weave test", data_dir=tmp_data_dir)
    evt = append_weave(capsule_id="c1", data_dir=tmp_data_dir)
    assert evt.episode_id == ep_id


def test_trace_file(tmp_data_dir: Path) -> None:
    append_weave(
        capsule_id="c1", affected_symbols=["src/auth.py:login"],
        episode_id="", data_dir=tmp_data_dir,
    )
    append_weave(
        capsule_id="c2", affected_symbols=["src/db.py:query"],
        episode_id="", data_dir=tmp_data_dir,
    )
    append_weave(
        capsule_id="c3", affected_symbols=["src/auth.py:logout"],
        episode_id="", data_dir=tmp_data_dir,
    )
    results = trace_file("src/auth.py", data_dir=tmp_data_dir)
    assert len(results) == 2
    assert results[0].capsule_id == "c1"
    assert results[1].capsule_id == "c3"


def test_export_md(tmp_data_dir: Path) -> None:
    ep_id = start_episode(title="export test", data_dir=tmp_data_dir)
    append_weave(
        capsule_id="c1", git_commit_sha="abc123",
        affected_symbols=["src/main.py:run"],
        data_dir=tmp_data_dir,
    )
    md = export_weave_md(episode_id=ep_id, data_dir=tmp_data_dir)
    assert "# Provenance Weave" in md
    assert ep_id in md
    assert "abc123" in md
    assert "src/main.py:run" in md


def test_weave_sequence_ids_monotonic(tmp_data_dir: Path) -> None:
    append_weave(capsule_id="c1", data_dir=tmp_data_dir)
    append_weave(capsule_id="c2", data_dir=tmp_data_dir)
    append_weave(capsule_id="c3", data_dir=tmp_data_dir)
    evts = db.list_weave_events(tmp_data_dir)
    assert [e.sequence_id for e in evts] == [1, 2, 3]


def test_verify_detects_sequence_gap(tmp_data_dir: Path) -> None:
    append_weave(capsule_id="c1", data_dir=tmp_data_dir)
    append_weave(capsule_id="c2", data_dir=tmp_data_dir)
    append_weave(capsule_id="c3", data_dir=tmp_data_dir)
    evts = db.list_weave_events(tmp_data_dir)
    conn = db.get_connection(tmp_data_dir)
    try:
        conn.execute(
            "UPDATE weave_events SET sequence_id = 7 WHERE event_id = ?",
            (evts[1].event_id,),
        )
        conn.commit()
    finally:
        conn.close()
    valid, err = verify_weave(tmp_data_dir)
    assert not valid
    assert "Sequence gap at" in err


def test_cli_weave_verify_emits_chain_break_event(tmp_data_dir: Path, monkeypatch) -> None:
    append_weave(capsule_id="c1", data_dir=tmp_data_dir)
    append_weave(capsule_id="c2", data_dir=tmp_data_dir)
    evts = db.list_weave_events(tmp_data_dir)
    conn = db.get_connection(tmp_data_dir)
    try:
        conn.execute(
            "UPDATE weave_events SET sequence_id = 5 WHERE event_id = ?",
            (evts[1].event_id,),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setenv("AGENTMESH_DATA_DIR", str(tmp_data_dir))
    monkeypatch.setenv("AGENTMESH_AGENT_ID", "test_agent")
    result = runner.invoke(app, ["weave", "verify"])
    assert result.exit_code == 1
    all_events = events.read_events(tmp_data_dir)
    chain_break = [e for e in all_events if e.kind == EventKind.WEAVE_CHAIN_BREAK]
    assert len(chain_break) == 1
    assert "Sequence gap" in chain_break[0].payload["error"]


def test_concurrent_append_assigns_unique_monotonic_sequences(tmp_data_dir: Path) -> None:
    def _append(i: int) -> int:
        evt = append_weave(capsule_id=f"c{i}", data_dir=tmp_data_dir)
        return evt.sequence_id

    with ThreadPoolExecutor(max_workers=8) as pool:
        seqs = list(pool.map(_append, range(30)))

    assert sorted(seqs) == list(range(1, 31))
    valid, err = verify_weave(tmp_data_dir)
    assert valid, err
