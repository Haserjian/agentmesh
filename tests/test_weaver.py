"""Tests for provenance weaver -- 6 scenarios."""

from __future__ import annotations

from pathlib import Path

from agentmesh import db
from agentmesh.weaver import append_weave, verify_weave, trace_file, export_weave_md
from agentmesh.episodes import start_episode


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
