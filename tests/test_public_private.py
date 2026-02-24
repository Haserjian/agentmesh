"""Tests for public/private classification helpers and CLI command."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from agentmesh.cli import app
from agentmesh.public_private import PRIVATE, PUBLIC, REVIEW, classify_path

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


def test_classify_path_public_private_review(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "docs").mkdir(parents=True)
    (repo / "src" / "ok.py").write_text("x = 1\n")
    (repo / "docs" / "note.md").write_text("plain docs\n")
    (repo / "docs" / "internal_strategy.md").write_text("go-to-market notes\n")

    pub = classify_path(repo / "src" / "ok.py", repo_root=repo)
    assert pub.classification == PUBLIC

    rev = classify_path(repo / "docs" / "note.md", repo_root=repo)
    assert rev.classification == REVIEW

    priv = classify_path(repo / "docs" / "internal_strategy.md", repo_root=repo)
    assert priv.classification == PRIVATE


def test_classify_cli_json_and_fail_codes(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    (repo / ".agentmesh" / "runs").mkdir(parents=True)
    (repo / "docs").mkdir(parents=True)
    (repo / ".agentmesh" / "runs" / "report.json").write_text("{}\n")
    (repo / "docs" / "note.md").write_text("safe note\n")

    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["classify", ".agentmesh/runs/report.json", "docs/note.md", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    by_path = {row["path"]: row for row in payload["results"]}
    assert by_path[".agentmesh/runs/report.json"]["classification"] == PRIVATE
    assert by_path["docs/note.md"]["classification"] == REVIEW

    fail_private = runner.invoke(app, ["classify", ".agentmesh/runs/report.json", "--fail-on-private"])
    assert fail_private.exit_code == 2

    fail_review = runner.invoke(app, ["classify", "docs/note.md", "--fail-on-review"])
    assert fail_review.exit_code == 3


def test_classify_cli_staged(tmp_path: Path, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    (repo / "docs").mkdir(parents=True, exist_ok=True)
    (repo / "docs" / "guide.md").write_text("guide\n")
    subprocess.run(["git", "add", "docs/guide.md"], cwd=str(repo), capture_output=True, check=True)

    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["classify", "--staged", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    rows = {row["path"]: row["classification"] for row in payload["results"]}
    assert rows["docs/guide.md"] == REVIEW


def test_classify_cli_staged_requires_git_repo(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "not-repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["classify", "--staged"])
    assert result.exit_code == 1
    assert "Not a git repository" in result.output


def test_classify_honors_policy_overrides(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    (repo / ".agentmesh").mkdir(parents=True)
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "note.md").write_text("safe\n")
    (repo / ".agentmesh" / "policy.json").write_text(
        json.dumps(
            {
                "public_private": {
                    "public_path_globs": ["docs/**"],
                    "private_path_globs": [],
                    "review_path_globs": [],
                    "private_content_patterns": [],
                }
            }
        )
    )

    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["classify", "docs/note.md", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["results"][0]["classification"] == PUBLIC


def test_classify_docs_public_json_as_public(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "alpha-gate-report.public.json").write_text("{}\n")
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["classify", "docs/alpha-gate-report.public.json", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["results"][0]["classification"] == PUBLIC
