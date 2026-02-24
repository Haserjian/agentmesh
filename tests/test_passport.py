"""Tests for thread passport (.meshpack) -- 5 scenarios."""

from __future__ import annotations

import tarfile
from pathlib import Path

from agentmesh import db
from agentmesh.episodes import start_episode, end_episode
from agentmesh.models import Agent
from agentmesh.messages import post
from agentmesh.weaver import append_weave
from agentmesh.passport import export_episode, verify_meshpack, import_meshpack


def _register(agent_id: str, data_dir: Path) -> None:
    db.register_agent(Agent(agent_id=agent_id, cwd="/tmp"), data_dir)


def _setup_episode(data_dir: Path) -> str:
    """Create an episode with some data, end it, return episode_id."""
    _register("a1", data_dir)
    ep_id = start_episode(title="Test Episode", data_dir=data_dir)
    post("a1", "hello from episode", data_dir=data_dir)
    append_weave(capsule_id="cap_test", git_commit_sha="abc123", data_dir=data_dir)
    end_episode(data_dir)
    return ep_id


def test_export_creates_file(tmp_data_dir: Path, tmp_path: Path) -> None:
    ep_id = _setup_episode(tmp_data_dir)
    out = tmp_path / "test.meshpack"
    result = export_episode(ep_id, output_path=out, data_dir=tmp_data_dir)
    assert result == out
    assert out.exists()
    assert out.stat().st_size > 0


def test_export_contains_expected_members(tmp_data_dir: Path, tmp_path: Path) -> None:
    ep_id = _setup_episode(tmp_data_dir)
    out = tmp_path / "test.meshpack"
    export_episode(ep_id, output_path=out, data_dir=tmp_data_dir)
    with tarfile.open(str(out), "r:gz") as tf:
        names = tf.getnames()
    assert "manifest.json" in names
    assert "capsules.jsonl" in names
    assert "claims_snapshot.jsonl" in names
    assert "messages.jsonl" in names
    assert "weave_slice.jsonl" in names


def test_verify_valid(tmp_data_dir: Path, tmp_path: Path) -> None:
    ep_id = _setup_episode(tmp_data_dir)
    out = tmp_path / "test.meshpack"
    export_episode(ep_id, output_path=out, data_dir=tmp_data_dir)
    valid, manifest = verify_meshpack(out)
    assert valid
    assert manifest["episode_id"] == ep_id
    assert manifest["counts"]["messages"] == 1
    assert manifest["counts"]["weave_events"] == 1


def test_verify_detects_tamper(tmp_data_dir: Path, tmp_path: Path) -> None:
    ep_id = _setup_episode(tmp_data_dir)
    out = tmp_path / "test.meshpack"
    export_episode(ep_id, output_path=out, data_dir=tmp_data_dir)

    # Tamper: rewrite messages.jsonl inside the archive
    import io
    import json

    with tarfile.open(str(out), "r:gz") as tf:
        members = {m.name: tf.extractfile(m).read() for m in tf.getmembers()}  # type: ignore

    members["messages.jsonl"] = b'{"msg_id":"fake","from_agent":"evil","body":"tampered"}\n'

    tampered = tmp_path / "tampered.meshpack"
    with tarfile.open(str(tampered), "w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

    valid, _ = verify_meshpack(tampered)
    assert not valid


def test_cross_db_import(tmp_data_dir: Path, tmp_path: Path) -> None:
    ep_id = _setup_episode(tmp_data_dir)
    out = tmp_path / "test.meshpack"
    export_episode(ep_id, output_path=out, data_dir=tmp_data_dir)

    # Import into a fresh DB
    new_data_dir = tmp_path / "new_mesh"
    new_data_dir.mkdir()
    db.init_db(new_data_dir)

    counts = import_meshpack(out, namespace="imported", data_dir=new_data_dir)
    assert counts["episodes"] == 1
    assert counts["messages"] == 1
    assert counts["weave_events"] == 1

    # Verify the episode exists in the new DB
    imported_ep = db.get_episode(f"imported/{ep_id}", new_data_dir)
    assert imported_ep is not None
    assert imported_ep.title == "Test Episode"
