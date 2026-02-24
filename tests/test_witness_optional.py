"""Optional dependency behavior for witness/key CLI commands."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

import agentmesh
from agentmesh.cli import app
from agentmesh.episodes import start_episode

runner = CliRunner()


def _init_repo(tmp_path: Path) -> Path:
    """Create a git repo with initial commit."""
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(tmp_path), capture_output=True, check=True)
    (tmp_path / "init.txt").write_text("init\n")
    subprocess.run(["git", "add", "init.txt"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True, check=True)
    return tmp_path


def _block_module(monkeypatch, module_name: str) -> None:
    """Force import of module_name to fail with ModuleNotFoundError."""
    submodule = module_name.rsplit(".", 1)[-1]
    if hasattr(agentmesh, submodule):
        monkeypatch.delattr(agentmesh, submodule, raising=False)
    monkeypatch.setitem(sys.modules, module_name, None)


def test_commit_without_witness_dependency_falls_back_to_episode_trailer(
    tmp_path: Path,
    tmp_data_dir: Path,
    monkeypatch,
) -> None:
    """Core commit should still work when witness deps are unavailable."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENTMESH_DATA_DIR", str(tmp_data_dir))
    monkeypatch.setenv("AGENTMESH_AGENT_ID", "fallback_agent")

    ep_id = start_episode(title="fallback commit", data_dir=tmp_data_dir)

    (repo / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "a.py"], cwd=str(repo), capture_output=True, check=True)

    _block_module(monkeypatch, "agentmesh.witness")

    result = runner.invoke(app, ["commit", "-m", "commit without witness deps"])
    assert result.exit_code == 0, result.output
    assert "Committed" in result.output
    assert "witness=" not in result.output

    log = subprocess.run(
        ["git", "log", "-1", "--format=%B"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert f"AgentMesh-Episode: {ep_id}" in log
    assert "AgentMesh-Witness:" not in log
    assert "AgentMesh-Sig:" not in log


def test_witness_verify_missing_dependency_shows_install_hint(monkeypatch) -> None:
    """witness verify should fail with a clear install hint when deps are missing."""
    _block_module(monkeypatch, "agentmesh.witness")
    result = runner.invoke(app, ["witness", "verify", "HEAD"])
    assert result.exit_code == 1
    assert "agentmesh-core[witness]" in result.output


def test_key_generate_missing_dependency_shows_install_hint(monkeypatch) -> None:
    """key generate should fail with a clear install hint when deps are missing."""
    _block_module(monkeypatch, "agentmesh.keystore")
    result = runner.invoke(app, ["key", "generate"])
    assert result.exit_code == 1
    assert "agentmesh-core[witness]" in result.output
