"""Tests for episode lifecycle -- 6 scenarios."""

from __future__ import annotations

from pathlib import Path

from agentmesh import db
from agentmesh.episodes import generate_episode_id, start_episode, get_current_episode, end_episode
from agentmesh.claims import make_claim
from agentmesh.messages import post
from agentmesh.models import Agent, Severity


def _register(agent_id: str, data_dir: Path) -> None:
    db.register_agent(Agent(agent_id=agent_id, cwd="/tmp"), data_dir)


def test_episode_id_format() -> None:
    eid = generate_episode_id()
    assert eid.startswith("ep_")
    assert len(eid) == 27  # "ep_" + 24 hex chars


def test_start_and_get(tmp_data_dir: Path) -> None:
    ep_id = start_episode(title="Auth module", data_dir=tmp_data_dir)
    assert ep_id.startswith("ep_")
    current = get_current_episode(tmp_data_dir)
    assert current == ep_id
    ep = db.get_episode(ep_id, tmp_data_dir)
    assert ep is not None
    assert ep.title == "Auth module"
    assert ep.ended_at == ""


def test_end_episode(tmp_data_dir: Path) -> None:
    ep_id = start_episode(title="test", data_dir=tmp_data_dir)
    ended = end_episode(tmp_data_dir)
    assert ended == ep_id
    current = get_current_episode(tmp_data_dir)
    assert current == ""
    ep = db.get_episode(ep_id, tmp_data_dir)
    assert ep is not None
    assert ep.ended_at != ""


def test_claim_auto_tags_episode(tmp_data_dir: Path) -> None:
    _register("a1", tmp_data_dir)
    ep_id = start_episode(title="auto-tag test", data_dir=tmp_data_dir)
    ok, clm, _ = make_claim("a1", "/tmp/foo.py", data_dir=tmp_data_dir)
    assert ok
    assert clm.episode_id == ep_id


def test_message_auto_tags_episode(tmp_data_dir: Path) -> None:
    _register("a1", tmp_data_dir)
    ep_id = start_episode(title="msg test", data_dir=tmp_data_dir)
    msg = post("a1", "hello", data_dir=tmp_data_dir)
    assert msg.episode_id == ep_id


def test_no_episode_default(tmp_data_dir: Path) -> None:
    _register("a1", tmp_data_dir)
    ok, clm, _ = make_claim("a1", "/tmp/bar.py", data_dir=tmp_data_dir)
    assert ok
    assert clm.episode_id == ""
    msg = post("a1", "no ep", data_dir=tmp_data_dir)
    assert msg.episode_id == ""
