"""Tests for portable witness signing and verification."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

# Witness features require cryptography; skip this module otherwise.
pytest.importorskip("cryptography")

from agentmesh.cli import app
from agentmesh import witness

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


def _latest_commit_message(repo: Path) -> str:
    return subprocess.run(
        ["git", "log", "-1", "--format=%B"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _latest_commit_sha(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_commit_includes_portable_witness_payload_trailers(
    tmp_path: Path,
    tmp_data_dir: Path,
    monkeypatch,
) -> None:
    """Witness-enabled commits should include inline portable witness trailers."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENTMESH_DATA_DIR", str(tmp_data_dir))
    monkeypatch.setenv("AGENTMESH_AGENT_ID", "witness_agent")

    assert runner.invoke(app, ["key", "generate"]).exit_code == 0
    assert runner.invoke(app, ["episode", "start", "--title", "portable trailers"]).exit_code == 0

    (repo / "x.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "x.py"], cwd=str(repo), capture_output=True, check=True)

    result = runner.invoke(app, ["commit", "-m", "add x"])
    assert result.exit_code == 0, result.output

    msg = _latest_commit_message(repo)
    assert "AgentMesh-Witness-Encoding: gzip+base64url" in msg
    assert "AgentMesh-Witness-Chunk-Count:" in msg
    assert "AgentMesh-Witness-Chunk:" in msg

    parsed = witness.parse_trailers(msg)
    chunk_count = int(parsed[witness.TRAILER_WITNESS_CHUNK_COUNT])
    chunks = parsed[witness.TRAILER_WITNESS_CHUNK]
    assert chunk_count == len(chunks)


def test_verify_works_without_sidecar_or_local_key(
    tmp_path: Path,
    tmp_data_dir: Path,
    monkeypatch,
) -> None:
    """Verification must work from trailer payload even without sidecar or key files."""
    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENTMESH_DATA_DIR", str(tmp_data_dir))
    monkeypatch.setenv("AGENTMESH_AGENT_ID", "witness_agent")

    assert runner.invoke(app, ["key", "generate"]).exit_code == 0
    assert runner.invoke(app, ["episode", "start", "--title", "portable verify"]).exit_code == 0

    (repo / "y.py").write_text("y = 2\n")
    subprocess.run(["git", "add", "y.py"], cwd=str(repo), capture_output=True, check=True)
    result = runner.invoke(app, ["commit", "-m", "add y"])
    assert result.exit_code == 0, result.output

    msg = _latest_commit_message(repo)
    parsed = witness.parse_trailers(msg)
    w_hash = parsed[witness.TRAILER_WITNESS]
    hash_hex = w_hash.split(":", 1)[1]

    # Remove local sidecar and local key files to prove fully portable verify.
    sidecar = tmp_data_dir / "witnesses" / f"{hash_hex}.json"
    if sidecar.exists():
        sidecar.unlink()
    for p in (tmp_data_dir / "keys").glob("*"):
        p.unlink()

    sha = _latest_commit_sha(repo)
    verify = witness.verify_commit(sha, cwd=str(repo), data_dir=tmp_data_dir)
    assert verify.ok, verify


def test_verify_detects_witness_hash_mismatch(
    tmp_path: Path,
    tmp_data_dir: Path,
    monkeypatch,
) -> None:
    """Tampering trailer witness hash should fail verification."""
    from agentmesh import gitbridge

    repo = _init_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENTMESH_DATA_DIR", str(tmp_data_dir))
    monkeypatch.setenv("AGENTMESH_AGENT_ID", "witness_agent")

    assert runner.invoke(app, ["key", "generate"]).exit_code == 0
    assert runner.invoke(app, ["episode", "start", "--title", "tamper test"]).exit_code == 0

    (repo / "z.py").write_text("z = 3\n")
    subprocess.run(["git", "add", "z.py"], cwd=str(repo), capture_output=True, check=True)

    created = witness.create_and_sign("witness_agent", cwd=str(repo), data_dir=tmp_data_dir)
    assert created is not None
    _w, w_hash, _sig, _kid, trailer = created
    tampered = trailer.replace(w_hash, f"sha256:{'0' * 64}", 1)

    ok, sha, err = gitbridge.git_commit("tampered witness hash", trailer=tampered, cwd=str(repo))
    assert ok, err

    verify = witness.verify_commit(sha, cwd=str(repo), data_dir=tmp_data_dir)
    assert verify.status == "WITNESS_HASH_MISMATCH"
