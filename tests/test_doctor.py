"""Tests for doctor preflight checks."""

from __future__ import annotations

import subprocess
from pathlib import Path

from typer.testing import CliRunner

from agentmesh.cli import app
from agentmesh.episodes import get_current_episode

runner = CliRunner()


def _init_repo(tmp_path: Path) -> Path:
    """Create a git repo with one initial commit."""
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(tmp_path), capture_output=True, check=True)
    (tmp_path / "init.txt").write_text("init\n")
    subprocess.run(["git", "add", "init.txt"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True, check=True)
    return tmp_path


def test_doctor_reports_active_episode(
    tmp_path: Path,
    tmp_data_dir: Path,
    monkeypatch,
) -> None:
    """doctor should report active episode when one exists."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENTMESH_DATA_DIR", str(tmp_data_dir))

    import agentmesh.hooks.install as hook_install
    monkeypatch.setattr(
        hook_install,
        "hooks_status",
        lambda: {"installed": True, "scripts_present": True, "settings_configured": True},
    )

    init_result = runner.invoke(app, ["init"])
    assert init_result.exit_code == 0, init_result.output

    start_result = runner.invoke(app, ["episode", "start", "--title", "doctor test"])
    assert start_result.exit_code == 0, start_result.output
    ep_id = get_current_episode(tmp_data_dir)
    assert ep_id

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert f"Active episode: {ep_id}" in result.output
    assert "Could not read database" not in result.output
