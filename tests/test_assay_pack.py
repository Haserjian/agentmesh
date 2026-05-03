"""Tests for AgentMesh -> Assay proof-pack adapter."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentmesh import db, events, episodes
from agentmesh.assay_pack import (
    build_agentmesh_assay_pack,
    build_agentmesh_receipts,
    build_outputs_manifest,
)
from agentmesh.models import EventKind


pytest.importorskip("assay.proof_pack")


def _init_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(tmp_path), capture_output=True, check=True)
    (tmp_path / "init.txt").write_text("init\n")
    subprocess.run(["git", "add", "init.txt"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True, check=True)
    return tmp_path


def test_outputs_manifest_is_stable_and_uses_assay_canon(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "b.txt").write_text("b\n")
    (outputs / "a.txt").write_text("a\n")
    (outputs / "__pycache__").mkdir()
    (outputs / "__pycache__" / "ignored.pyc").write_bytes(b"ignored")

    first = build_outputs_manifest(outputs)
    second = build_outputs_manifest(outputs)

    assert first == second
    assert first["canon"] == "assay.jcs-rfc8785"
    assert first["root_digest"].startswith("sha256:")
    assert [item["path"] for item in first["files"]] == ["a.txt", "b.txt"]


def test_build_agentmesh_receipts_projects_guardian_decisions(
    tmp_path: Path,
    tmp_data_dir: Path,
) -> None:
    repo = _init_repo(tmp_path / "repo")
    episode_id = episodes.start_episode("adapter test", data_dir=tmp_data_dir)
    events.append_event(
        EventKind.ASSAY_RECEIPT,
        agent_id="agent_1",
        payload={
            "_ewp_episode_id": episode_id,
            "_ewp_origin": "agentmesh/assay_commit_hook",
            "sha": "a" * 40,
            "ok": False,
            "returncode": 126,
            "guardian_decision_receipt": {
                "schema": "agentmesh.guardian_tool_decision/v1",
                "tool_id": "agentmesh.assay_hook",
                "command_id": "rejected",
                "input_origin": "model_suggested",
                "guardian_decision": "deny",
                "decision_reason": "shell_metacharacter_forbidden",
                "argv_digest": "sha256:" + "1" * 64,
                "argv_redacted": [],
                "policy_version": "agentmesh.assay_hook/v1",
            },
        },
        data_dir=tmp_data_dir,
    )

    receipts = build_agentmesh_receipts(
        episode_id,
        data_dir=tmp_data_dir,
        repo_path=repo,
        outputs_manifest={"schema": "agentmesh.outputs_manifest/v1", "root_digest": "sha256:" + "2" * 64},
    )

    receipt_types = {receipt["type"] for receipt in receipts}
    assert "agentmesh.episode/v1" in receipt_types
    assert "agentmesh.output_manifest/v1" in receipt_types
    assert "agentmesh.guardian_decision/v1" in receipt_types
    guardian = next(receipt for receipt in receipts if receipt["type"] == "agentmesh.guardian_decision/v1")
    assert guardian["payload"]["assay_receipt"]["guardian_decision_receipt"]["guardian_decision"] == "deny"
    assert "stdout" not in guardian["payload"]["assay_receipt"]


def test_build_agentmesh_assay_pack_uses_assay_verifier(
    tmp_path: Path,
    tmp_data_dir: Path,
) -> None:
    from assay.keystore import AssayKeyStore, DEFAULT_SIGNER_ID
    from assay.proof_pack import verify_proof_pack

    repo = _init_repo(tmp_path / "repo")
    outputs = repo / "outputs"
    outputs.mkdir()
    (outputs / "episode_output.json").write_text('{"ok":true}\n')
    results = repo / "results.xml"
    results.write_text("<testsuite tests='1' failures='0'></testsuite>\n")
    script = repo / "episode.sh"
    script.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
OUT="${AGENTMESH_OUTPUTS_DIR:-outputs}"
RESULTS="${AGENTMESH_RESULTS_PATH:-results.xml}"
mkdir -p "$OUT"
printf '{"ok":true}\\n' > "$OUT/episode_output.json"
printf "<testsuite tests='1' failures='0'></testsuite>\\n" > "$RESULTS"
"""
    )

    episode_id = episodes.start_episode("pack build", data_dir=tmp_data_dir)
    events.append_event(
        EventKind.ASSAY_RECEIPT,
        agent_id="agent_1",
        payload={
            "_ewp_episode_id": episode_id,
            "_ewp_origin": "agentmesh/assay_commit_hook",
            "command_id": "assay-gate-check",
            "tool_id": "agentmesh.assay_hook",
            "ok": True,
            "returncode": 0,
            "guardian_decision_receipt": {
                "schema": "agentmesh.guardian_tool_decision/v1",
                "tool_id": "agentmesh.assay_hook",
                "command_id": "assay-gate-check",
                "input_origin": "repo_policy",
                "guardian_decision": "allow",
                "decision_reason": "registered_assay_command_preset",
                "argv_digest": "sha256:" + "3" * 64,
                "argv_redacted": ["assay", "gate", "check", "<repo>", "--min-score", "0", "--json"],
                "policy_version": "agentmesh.assay_hook/v1",
            },
        },
        data_dir=tmp_data_dir,
    )

    keystore = AssayKeyStore(keys_dir=tmp_path / "keys")
    keystore.ensure_key(DEFAULT_SIGNER_ID)
    result = build_agentmesh_assay_pack(
        episode_id,
        tmp_path / "proof_pack",
        data_dir=tmp_data_dir,
        repo_path=repo,
        outputs_dir=outputs,
        results_path=results,
        episode_script=script,
        keystore=keystore,
    )

    manifest = json.loads((result.pack_dir / "pack_manifest.json").read_text())
    verify = verify_proof_pack(manifest, result.pack_dir, keystore)
    assert verify.passed, [error.to_dict() for error in verify.errors]

    cli_verify = subprocess.run(
        [sys.executable, "-m", "assay.cli", "verify-pack", str(result.pack_dir), "--json"],
        capture_output=True,
        text=True,
    )
    assert cli_verify.returncode == 0, cli_verify.stdout + cli_verify.stderr

    receipt_lines = [
        json.loads(line)
        for line in (result.pack_dir / "receipt_pack.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert {receipt["type"] for receipt in receipt_lines} >= {
        "agentmesh.episode/v1",
        "agentmesh.output_manifest/v1",
        "agentmesh.result_artifact/v1",
        "agentmesh.guardian_decision/v1",
    }

    sidecar_manifest = json.loads(
        (result.pack_dir / "_unsigned" / "agentmesh" / "outputs_manifest.json").read_text()
    )
    output_receipt = next(receipt for receipt in receipt_lines if receipt["type"] == "agentmesh.output_manifest/v1")
    assert sidecar_manifest["root_digest"] == result.output_root_digest
    assert output_receipt["payload"]["outputs_manifest"]["root_digest"] == result.output_root_digest

    replay = result.pack_dir / "_unsigned" / "agentmesh" / "replay-from-proof.sh"
    assert replay.exists()
    assert (result.pack_dir / "_unsigned" / "agentmesh" / "replay" / "episode.sh").exists()
    sidecar_manifest_path = result.pack_dir / "_unsigned" / "agentmesh" / "outputs_manifest.json"
    sidecar_manifest_path.write_text('{"tampered": true}\n')
    replay_result = subprocess.run(
        [
            "bash",
            str(replay.relative_to(result.pack_dir.parent)),
            result.pack_dir.name,
        ],
        cwd=result.pack_dir.parent,
        capture_output=True,
        text=True,
    )
    assert replay_result.returncode == 0, replay_result.stdout + replay_result.stderr
    assert "valid:true" in replay_result.stdout
