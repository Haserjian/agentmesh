"""Tests for the AgentMesh replay proto contract.

The proto deliberately references Assay output/proof-pack hashes. It must not
become a second proof-pack or artifact container.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


PROTO_PATH = Path(__file__).parents[1] / "proto" / "execution.proto"


def _field_names(message_body: str) -> set[str]:
    return set(re.findall(r"\b(?:string|int32|repeated string|map<string, string>)\s+([a-z0-9_]+)\s*=", message_body))


def test_execution_proto_is_hash_reference_contract() -> None:
    proto = PROTO_PATH.read_text()
    assert "message ExecutionRequest" in proto
    assert "message ExecutionResult" in proto
    assert "repeated bytes artifacts" not in proto
    assert "repeated bytes signatures" not in proto

    request_body = proto.split("message ExecutionRequest", 1)[1].split("message ExecutionResult", 1)[0]
    result_body = proto.split("message ExecutionResult", 1)[1]

    assert _field_names(request_body) >= {
        "request_id",
        "repo",
        "commit_sha",
        "command",
        "env",
        "assay_pack_format",
    }
    assert _field_names(result_body) == {
        "request_id",
        "exit_code",
        "output_manifest_sha256",
        "proof_pack_root_sha256",
    }


def test_replay_result_hashes_reference_existing_assay_output_manifest(tmp_path: Path) -> None:
    pytest.importorskip("assay.proof_pack")
    from agentmesh.assay_pack import build_outputs_manifest

    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "episode_output.json").write_text('{"ok":true}\n')

    outputs_manifest = build_outputs_manifest(outputs)
    proof_pack_root = "b" * 64
    replay_result = {
        "request_id": "req_1",
        "exit_code": 0,
        "output_manifest_sha256": outputs_manifest["root_digest"].removeprefix("sha256:"),
        "proof_pack_root_sha256": proof_pack_root,
    }

    assert len(replay_result["output_manifest_sha256"]) == 64
    assert replay_result["output_manifest_sha256"] == outputs_manifest["root_digest"].split("sha256:", 1)[1]
    assert replay_result["proof_pack_root_sha256"] == proof_pack_root
