"""Tests for context capsules."""

from __future__ import annotations

import json
from pathlib import Path

from agentmesh import db
from agentmesh.capsules import build_capsule, get_capsule_bundle
from agentmesh.claims import make_claim
from agentmesh.messages import post
from agentmesh.models import Agent


def _register(agent_id: str, data_dir: Path) -> None:
    db.register_agent(Agent(agent_id=agent_id, cwd="/tmp"), data_dir)


def test_build_capsule(tmp_data_dir: Path) -> None:
    _register("a1", tmp_data_dir)
    cap = build_capsule("a1", task_desc="Test task", data_dir=tmp_data_dir)
    assert cap.capsule_id.startswith("cap_")
    assert cap.agent_id == "a1"
    assert cap.task_desc == "Test task"


def test_capsule_saved_to_db(tmp_data_dir: Path) -> None:
    _register("a1", tmp_data_dir)
    cap = build_capsule("a1", task_desc="DB test", data_dir=tmp_data_dir)
    got = db.get_capsule(cap.capsule_id, tmp_data_dir)
    assert got is not None
    assert got.task_desc == "DB test"


def test_capsule_bundle_json(tmp_data_dir: Path) -> None:
    _register("a1", tmp_data_dir)
    make_claim("a1", "/tmp/foo.py", data_dir=tmp_data_dir)
    post("a1", "working on foo", data_dir=tmp_data_dir)
    cap = build_capsule("a1", task_desc="Foo module", data_dir=tmp_data_dir)

    bundle = get_capsule_bundle(cap.capsule_id, data_dir=tmp_data_dir)
    assert bundle is not None
    assert bundle["capsule_id"] == cap.capsule_id
    assert bundle["task_desc"] == "Foo module"
    assert "git" in bundle
    assert "mesh" in bundle
    assert len(bundle["mesh"]["open_claims"]) == 1
    assert len(bundle["mesh"]["recent_messages"]) >= 1
    assert len(bundle["mesh"]["active_agents"]) == 1


def test_sbar_fields_populated(tmp_data_dir: Path) -> None:
    """SBAR dict should contain all four sections."""
    _register("a1", tmp_data_dir)
    make_claim("a1", "/tmp/foo.py", data_dir=tmp_data_dir)
    cap = build_capsule("a1", task_desc="Auth module", data_dir=tmp_data_dir)
    sbar = cap.sbar
    assert "situation" in sbar
    assert "background" in sbar
    assert "assessment" in sbar
    assert "recommendation" in sbar
    assert sbar["situation"]["global_objective"] == "Auth module"
    assert sbar["assessment"]["test_status"] == "unknown"
    assert isinstance(sbar["assessment"]["open_claims"], list)
    assert len(sbar["assessment"]["open_claims"]) == 1


def test_sbar_backward_compat_flat_fields(tmp_data_dir: Path) -> None:
    """Old capsule fields (what_changed, risks, etc.) should still work alongside SBAR."""
    _register("a1", tmp_data_dir)
    cap = build_capsule("a1", task_desc="Test", data_dir=tmp_data_dir)
    # Flat fields still present
    assert cap.test_status == "unknown"
    assert cap.what_changed == ""
    assert cap.risks == []
    # SBAR also present
    assert isinstance(cap.sbar, dict)
    assert "situation" in cap.sbar


def test_bundle_json_includes_sbar(tmp_data_dir: Path) -> None:
    """Bundle JSON file should include the sbar key."""
    _register("a1", tmp_data_dir)
    cap = build_capsule("a1", task_desc="Bundle SBAR", data_dir=tmp_data_dir)
    bundle = get_capsule_bundle(cap.capsule_id, data_dir=tmp_data_dir)
    assert bundle is not None
    assert "sbar" in bundle
    assert bundle["sbar"]["situation"]["global_objective"] == "Bundle SBAR"
