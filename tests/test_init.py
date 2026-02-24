"""Tests for repository init scaffolding."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agentmesh.cli import app

runner = CliRunner()


def test_init_writes_scaffold_files(tmp_path: Path) -> None:
    """init should write AGENTS, capabilities, and policy with requested defaults."""
    repo = tmp_path / "repo"
    repo.mkdir()

    result = runner.invoke(
        app,
        [
            "init",
            "--repo",
            str(repo),
            "--test-command",
            "pytest -q tests/unit",
            "--claim-ttl",
            "900",
            "--no-capsule-default",
        ],
    )
    assert result.exit_code == 0, result.output

    agents_path = repo / "AGENTS.md"
    caps_path = repo / ".agentmesh" / "capabilities.json"
    policy_path = repo / ".agentmesh" / "policy.json"
    assert agents_path.exists()
    assert caps_path.exists()
    assert policy_path.exists()

    agents_text = agents_path.read_text()
    assert "claim TTL: `900` seconds" in agents_text
    assert "task finish test command: `pytest -q tests/unit`" in agents_text
    assert "capsule default: `false`" in agents_text

    caps = json.loads(caps_path.read_text())
    assert caps["recommended_defaults"]["claim_ttl_seconds"] == 900
    assert caps["recommended_defaults"]["test_command"] == "pytest -q tests/unit"
    assert caps["recommended_defaults"]["capsule_on_finish"] is False

    policy = json.loads(policy_path.read_text())
    assert policy["claims"]["ttl_seconds"] == 900
    assert policy["task_finish"]["run_tests"] == "pytest -q tests/unit"
    assert policy["task_finish"]["capsule"] is False


def test_init_skips_existing_without_force(tmp_path: Path) -> None:
    """init should not overwrite scaffold files unless --force is provided."""
    repo = tmp_path / "repo"
    repo.mkdir()

    agents_path = repo / "AGENTS.md"
    agents_path.write_text("custom\n")

    result = runner.invoke(app, ["init", "--repo", str(repo)])
    assert result.exit_code == 0, result.output
    assert "skipped" in result.output
    assert agents_path.read_text() == "custom\n"

    forced = runner.invoke(app, ["init", "--repo", str(repo), "--force"])
    assert forced.exit_code == 0, forced.output
    assert agents_path.read_text() != "custom\n"
