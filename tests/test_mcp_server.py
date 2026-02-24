"""Tests for the AgentMesh MCP server tools."""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

# Skip entire module if mcp not installed
mcp_pkg = pytest.importorskip("mcp")

from agentmesh.mcp_server import (
    mesh_claim,
    mesh_release,
    mesh_check,
    mesh_status,
    mesh_episode_start,
    mesh_episode_end,
    mcp,
)
from agentmesh import db


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Each test gets its own data dir."""
    monkeypatch.setenv("AGENTMESH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTMESH_AGENT_ID", "test_agent")
    # init db for each test
    db.init_db(tmp_path)


# -- Tool registration --

def test_tools_registered():
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "mesh_claim", "mesh_release", "mesh_check",
        "mesh_status", "mesh_episode_start", "mesh_episode_end",
    }


# -- mesh_claim --

def test_claim_success():
    result = mesh_claim(resource="src/main.py")
    assert result["ok"] is True
    assert result["resource"].endswith("src/main.py")
    assert result["agent_id"] == "test_agent"
    assert "claim_id" in result


def test_claim_conflict():
    mesh_claim(resource="src/main.py", agent_id="agent_a")
    result = mesh_claim(resource="src/main.py", agent_id="agent_b")
    assert result["ok"] is False
    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["agent_id"] == "agent_a"


def test_claim_same_agent_no_conflict():
    mesh_claim(resource="src/main.py", agent_id="agent_a")
    result = mesh_claim(resource="src/main.py", agent_id="agent_a")
    assert result["ok"] is True


def test_claim_force():
    mesh_claim(resource="src/main.py", agent_id="agent_a")
    result = mesh_claim(resource="src/main.py", agent_id="agent_b", force=True)
    assert result["ok"] is True
    assert result["agent_id"] == "agent_b"


def test_claim_port_resource():
    result = mesh_claim(resource="PORT:3000")
    assert result["ok"] is True
    assert result["resource_type"] == "port"


def test_claim_lock_resource():
    result = mesh_claim(resource="LOCK:npm")
    assert result["ok"] is True
    assert result["resource_type"] == "lock"


# -- mesh_release --

def test_release_specific():
    mesh_claim(resource="src/main.py")
    result = mesh_release(resource="src/main.py")
    assert result["ok"] is True
    assert result["released_count"] == 1


def test_release_all():
    mesh_claim(resource="src/a.py")
    mesh_claim(resource="src/b.py")
    result = mesh_release(release_all=True)
    assert result["ok"] is True
    assert result["released_count"] == 2


def test_release_no_args():
    result = mesh_release()
    assert result["ok"] is False


# -- mesh_check --

def test_check_no_conflict():
    result = mesh_check(resource="src/main.py")
    assert result["claimed"] is False


def test_check_with_conflict():
    mesh_claim(resource="src/main.py", agent_id="agent_a")
    result = mesh_check(resource="src/main.py", agent_id="agent_b")
    assert result["claimed"] is True
    assert result["holders"][0]["agent_id"] == "agent_a"


def test_check_excludes_own():
    mesh_claim(resource="src/main.py", agent_id="agent_a")
    result = mesh_check(resource="src/main.py", agent_id="agent_a")
    assert result["claimed"] is False


# -- mesh_status --

def test_status_empty():
    result = mesh_status()
    assert "agents" in result
    assert "claims" in result
    assert result["current_episode"] is None


def test_status_with_claims():
    mesh_claim(resource="src/main.py")
    result = mesh_status()
    assert len(result["claims"]) == 1
    assert result["claims"][0]["path"].endswith("src/main.py")


def test_status_with_episode():
    mesh_episode_start(title="test work")
    result = mesh_status()
    assert result["current_episode"] is not None


# -- mesh_episode_start --

def test_episode_start():
    result = mesh_episode_start(title="fix bug")
    assert result["ok"] is True
    assert result["reused"] is False
    assert result["episode_id"].startswith("ep_")


def test_episode_start_reuses_existing():
    r1 = mesh_episode_start(title="first")
    r2 = mesh_episode_start(title="second")
    assert r2["reused"] is True
    assert r2["episode_id"] == r1["episode_id"]


# -- mesh_episode_end --

def test_episode_end():
    mesh_episode_start(title="task")
    result = mesh_episode_end()
    assert result["ok"] is True
    assert result["episode_id"].startswith("ep_")


def test_episode_end_no_active():
    result = mesh_episode_end()
    assert result["ok"] is False


# -- Integration: claim + episode --

def test_claim_during_episode():
    mesh_episode_start(title="coordinated task")
    result = mesh_claim(resource="src/auth.py", reason="fixing auth bug")
    assert result["ok"] is True
    status = mesh_status()
    assert status["current_episode"] is not None
    assert len(status["claims"]) == 1


def test_full_workflow():
    """Happy path: start episode -> claim -> check -> release -> end."""
    ep = mesh_episode_start(title="full workflow test")
    assert ep["ok"]

    c = mesh_claim(resource="src/app.py", agent_id="alice")
    assert c["ok"]

    # Bob checks -- sees Alice's claim
    chk = mesh_check(resource="src/app.py", agent_id="bob")
    assert chk["claimed"]

    # Alice releases
    rel = mesh_release(resource="src/app.py", agent_id="alice")
    assert rel["released_count"] == 1

    # Bob can now claim
    c2 = mesh_claim(resource="src/app.py", agent_id="bob")
    assert c2["ok"]

    # Clean up
    mesh_release(release_all=True, agent_id="bob")
    end = mesh_episode_end()
    assert end["ok"]
