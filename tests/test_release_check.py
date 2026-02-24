"""Tests for release-check deterministic preflight."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from agentmesh import db, weaver
from agentmesh.cli import app

runner = CliRunner()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(repo), capture_output=True, check=True)
    (repo / "init.txt").write_text("init\n")
    subprocess.run(["git", "add", "init.txt"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True, check=True)
    return repo


def test_release_check_passes_with_public_staged_file(
    tmp_path: Path,
    tmp_data_dir: Path,
    monkeypatch,
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "ok.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "src/ok.py"], cwd=str(repo), capture_output=True, check=True)

    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENTMESH_DATA_DIR", str(tmp_data_dir))
    result = runner.invoke(app, ["release-check", "--staged", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["release_check"]["ok"] is True


def test_release_check_fails_on_private_file(
    tmp_path: Path,
    tmp_data_dir: Path,
    monkeypatch,
) -> None:
    repo = _init_repo(tmp_path)
    (repo / ".agentmesh" / "runs").mkdir(parents=True, exist_ok=True)
    (repo / ".agentmesh" / "runs" / "report.json").write_text("{}\n")
    subprocess.run(
        ["git", "add", "-f", ".agentmesh/runs/report.json"],
        cwd=str(repo),
        capture_output=True,
        check=True,
    )

    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENTMESH_DATA_DIR", str(tmp_data_dir))
    result = runner.invoke(app, ["release-check", "--staged", "--json"])
    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["release_check"]["classification"]["private"] >= 1


def test_release_check_fails_on_review_file(
    tmp_path: Path,
    tmp_data_dir: Path,
    monkeypatch,
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "docs").mkdir(parents=True, exist_ok=True)
    (repo / "docs" / "guide.md").write_text("guide\n")
    subprocess.run(["git", "add", "docs/guide.md"], cwd=str(repo), capture_output=True, check=True)

    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENTMESH_DATA_DIR", str(tmp_data_dir))
    result = runner.invoke(app, ["release-check", "--staged", "--json"])
    assert result.exit_code == 3, result.output
    payload = json.loads(result.output)
    assert payload["release_check"]["classification"]["review"] >= 1


def test_release_check_fails_on_weave_verification(
    tmp_path: Path,
    tmp_data_dir: Path,
    monkeypatch,
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "ok.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "src/ok.py"], cwd=str(repo), capture_output=True, check=True)

    # Create and then corrupt a weave event.
    evt = weaver.append_weave(trace_id="release-check-test", data_dir=tmp_data_dir)
    conn = db.get_connection(tmp_data_dir)
    try:
        conn.execute(
            "UPDATE weave_events SET event_hash = ? WHERE event_id = ?",
            ("sha256:deadbeef", evt.event_id),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENTMESH_DATA_DIR", str(tmp_data_dir))
    result = runner.invoke(app, ["release-check", "--staged", "--json"])
    assert result.exit_code == 4, result.output
    payload = json.loads(result.output)
    assert payload["release_check"]["weave_verify"]["ok"] is False


def test_release_check_fails_when_witness_required_without_verified_witness(
    tmp_path: Path,
    tmp_data_dir: Path,
    monkeypatch,
) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENTMESH_DATA_DIR", str(tmp_data_dir))
    result = runner.invoke(app, ["release-check", "--staged", "--require-witness", "--json"])
    assert result.exit_code == 5, result.output
    payload = json.loads(result.output)
    assert payload["release_check"]["witness"]["status"] != "VERIFIED"


def test_release_check_fails_when_run_tests_command_fails(
    tmp_path: Path,
    tmp_data_dir: Path,
    monkeypatch,
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "ok.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "src/ok.py"], cwd=str(repo), capture_output=True, check=True)

    monkeypatch.chdir(repo)
    monkeypatch.setenv("AGENTMESH_DATA_DIR", str(tmp_data_dir))
    result = runner.invoke(
        app,
        [
            "release-check",
            "--staged",
            "--run-tests",
            'python -c "import sys; sys.exit(1)"',
            "--json",
        ],
    )
    assert result.exit_code == 6, result.output
    payload = json.loads(result.output)
    assert payload["release_check"]["tests"]["ok"] is False
