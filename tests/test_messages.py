"""Tests for message board."""

from __future__ import annotations

from pathlib import Path

from agentmesh import db
from agentmesh.messages import post, inbox
from agentmesh.models import Agent, Severity


def _register(agent_id: str, data_dir: Path) -> None:
    db.register_agent(Agent(agent_id=agent_id, cwd="/tmp"), data_dir)


def test_post_and_list(tmp_data_dir: Path) -> None:
    _register("a1", tmp_data_dir)
    msg = post("a1", "hello world", data_dir=tmp_data_dir)
    assert msg.body == "hello world"
    msgs = inbox(data_dir=tmp_data_dir)
    assert len(msgs) == 1
    assert msgs[0].from_agent == "a1"


def test_severity_filter(tmp_data_dir: Path) -> None:
    _register("a1", tmp_data_dir)
    post("a1", "fyi msg", severity=Severity.FYI, data_dir=tmp_data_dir)
    post("a1", "blocker msg", severity=Severity.BLOCKER, data_dir=tmp_data_dir)
    blockers = inbox(severity=Severity.BLOCKER, data_dir=tmp_data_dir)
    assert len(blockers) == 1
    assert blockers[0].body == "blocker msg"


def test_channel_filter(tmp_data_dir: Path) -> None:
    _register("a1", tmp_data_dir)
    post("a1", "general msg", channel="general", data_dir=tmp_data_dir)
    post("a1", "dev msg", channel="dev", data_dir=tmp_data_dir)
    dev_msgs = inbox(channel="dev", data_dir=tmp_data_dir)
    assert len(dev_msgs) == 1
    assert dev_msgs[0].body == "dev msg"


def test_mark_read(tmp_data_dir: Path) -> None:
    _register("a1", tmp_data_dir)
    _register("a2", tmp_data_dir)
    msg = post("a1", "read me", data_dir=tmp_data_dir)
    db.mark_read(msg.msg_id, "a2", tmp_data_dir)
    unread = inbox(agent_id="a2", unread=True, data_dir=tmp_data_dir)
    assert len(unread) == 0
