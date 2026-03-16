"""Tests for provenance export (AM-ASSAY-001).

Validates that AgentMesh weave events and witness signatures export
as Assay-compatible receipt dicts that survive proof pack verification.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import pytest

from agentmesh.models import WeaveEvent
from agentmesh.provenance_export import (
    weave_event_to_receipt,
    witness_to_receipt,
    export_episode_provenance,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_weave_event(**overrides) -> WeaveEvent:
    defaults = dict(
        event_id="weave_abc123def456",
        sequence_id=1,
        episode_id="ep_019cf5ac9f767b127bf41c8c",
        prev_hash="sha256:" + "0" * 64,
        capsule_id="",
        git_commit_sha="f2b0b26ccfd472f9cda0df2c42a85eb358276f09",
        git_patch_hash="sha256:cba7ec7cdf772f4562665357de6d44438ab97d84",
        affected_symbols=["src/auth.py", "tests/test_auth.py"],
        trace_id="",
        parent_event_id="",
        event_hash="sha256:38f92b23bcb51c9bf5231f7336c508a2ba4cf788",
        created_at="2026-03-16T12:00:00.000000+00:00",
    )
    defaults.update(overrides)
    return WeaveEvent(**defaults)


def _make_witness(**overrides) -> Dict:
    defaults = dict(
        schema_version="cwe_v1",
        episode_id="ep_019cf5ac9f767b127bf41c8c",
        patch_id_stable="ca5682d1d869a11c9bcca0889a679c72c9ce1d00",
        patch_hash_verbatim="sha256:d5401afbab4d12fb14569c984568eaa2b339",
        files_count=2,
        files_hash="sha256:f56e4f731d5a52251ea1e95dcce4fae800df60f8",
        agent_id="claude_0f0bf95c",
        timestamp="2026-03-16T12:01:00.000000+00:00",
        signer={
            "algorithm": "ed25519",
            "key_id": "mesh_a08cfb329abb0105",
            "public_key": "uSktno_jXK5nMTtxvwwOQZez6PGxI10-lOp6Bd9lFU0=",
        },
        tool_versions={"agentmesh": "0.9.0"},
    )
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Weave event receipt shape
# ---------------------------------------------------------------------------

class TestWeaveEventReceipt:
    """Verify weave events produce valid Assay receipt dicts."""

    def test_has_required_assay_fields(self):
        r = weave_event_to_receipt(_make_weave_event())
        assert "receipt_id" in r
        assert "type" in r
        assert "timestamp" in r

    def test_receipt_id_prefixed(self):
        r = weave_event_to_receipt(_make_weave_event())
        assert r["receipt_id"].startswith("amesh_")

    def test_type_is_agentmesh_weave(self):
        r = weave_event_to_receipt(_make_weave_event())
        assert r["type"] == "agentmesh_weave"

    def test_timestamp_from_event(self):
        r = weave_event_to_receipt(_make_weave_event())
        assert r["timestamp"] == "2026-03-16T12:00:00.000000+00:00"

    def test_hash_chain_preserved(self):
        r = weave_event_to_receipt(_make_weave_event())
        assert r["event_hash"].startswith("sha256:")
        assert r["prev_hash"].startswith("sha256:")

    def test_git_binding_present(self):
        r = weave_event_to_receipt(_make_weave_event())
        assert r["git_commit_sha"] == "f2b0b26ccfd472f9cda0df2c42a85eb358276f09"
        assert r["git_patch_hash"].startswith("sha256:")

    def test_affected_files_present(self):
        r = weave_event_to_receipt(_make_weave_event())
        assert "src/auth.py" in r["affected_files"]

    def test_episode_id_present(self):
        r = weave_event_to_receipt(_make_weave_event())
        assert r["episode_id"] == "ep_019cf5ac9f767b127bf41c8c"

    def test_seq_offset_applied(self):
        r = weave_event_to_receipt(_make_weave_event(sequence_id=3), seq_offset=1000)
        assert r["seq"] == 1003

    def test_json_serializable(self):
        r = weave_event_to_receipt(_make_weave_event())
        serialized = json.dumps(r)
        roundtripped = json.loads(serialized)
        assert roundtripped["receipt_id"] == r["receipt_id"]


# ---------------------------------------------------------------------------
# Witness receipt shape
# ---------------------------------------------------------------------------

class TestWitnessReceipt:
    """Verify witness dicts produce valid Assay receipt dicts."""

    def test_has_required_assay_fields(self):
        r = witness_to_receipt(_make_witness())
        assert "receipt_id" in r
        assert "type" in r
        assert "timestamp" in r

    def test_type_is_agentmesh_witness(self):
        r = witness_to_receipt(_make_witness())
        assert r["type"] == "agentmesh_witness"

    def test_signer_identity_present(self):
        r = witness_to_receipt(_make_witness())
        assert r["signer_algorithm"] == "ed25519"
        assert r["signer_key_id"] == "mesh_a08cfb329abb0105"
        assert len(r["signer_public_key"]) > 0

    def test_patch_binding_present(self):
        r = witness_to_receipt(_make_witness())
        assert r["patch_id_stable"] != ""
        assert r["patch_hash_verbatim"].startswith("sha256:")

    def test_episode_id_present(self):
        r = witness_to_receipt(_make_witness())
        assert r["episode_id"] == "ep_019cf5ac9f767b127bf41c8c"

    def test_json_serializable(self):
        r = witness_to_receipt(_make_witness())
        serialized = json.dumps(r)
        roundtripped = json.loads(serialized)
        assert roundtripped["type"] == "agentmesh_witness"

    def test_receipt_id_is_deterministic(self):
        """Same witness input must produce the same receipt_id every time."""
        w = _make_witness()
        r1 = witness_to_receipt(w)
        r2 = witness_to_receipt(w)
        assert r1["receipt_id"] == r2["receipt_id"]

    def test_different_witness_produces_different_id(self):
        """Changed witness content must produce a different receipt_id."""
        w1 = _make_witness()
        w2 = _make_witness(agent_id="different_agent_999")
        r1 = witness_to_receipt(w1)
        r2 = witness_to_receipt(w2)
        assert r1["receipt_id"] != r2["receipt_id"]

    def test_full_export_deterministic(self):
        """Identical inputs produce byte-identical export outputs."""
        events = [_make_weave_event()]
        witnesses = [_make_witness()]
        export1 = export_episode_provenance(events, witnesses)
        export2 = export_episode_provenance(events, witnesses)
        assert json.dumps(export1, sort_keys=True) == json.dumps(export2, sort_keys=True)


# ---------------------------------------------------------------------------
# Episode provenance export
# ---------------------------------------------------------------------------

class TestExportEpisodeProvenance:
    """Verify full episode export produces correct receipt list."""

    def test_weave_only_export(self):
        events = [
            _make_weave_event(event_id="weave_001", sequence_id=1),
            _make_weave_event(event_id="weave_002", sequence_id=2),
        ]
        receipts = export_episode_provenance(events)
        assert len(receipts) == 2
        assert all(r["type"] == "agentmesh_weave" for r in receipts)

    def test_weave_plus_witness_export(self):
        events = [_make_weave_event()]
        witnesses = [_make_witness()]
        receipts = export_episode_provenance(events, witnesses)
        assert len(receipts) == 2
        types = {r["type"] for r in receipts}
        assert types == {"agentmesh_weave", "agentmesh_witness"}

    def test_empty_export(self):
        receipts = export_episode_provenance([])
        assert receipts == []

    def test_seq_ordering_preserves_causality(self):
        events = [
            _make_weave_event(event_id="weave_001", sequence_id=1),
            _make_weave_event(event_id="weave_002", sequence_id=2),
        ]
        witnesses = [_make_witness()]
        receipts = export_episode_provenance(events, witnesses, seq_offset=1000)
        seqs = [r["seq"] for r in receipts]
        assert seqs == sorted(seqs), "Receipts must be causally ordered by seq"
        # Witness comes after weave events
        assert receipts[-1]["type"] == "agentmesh_witness"

    def test_all_receipts_json_serializable(self):
        events = [_make_weave_event()]
        witnesses = [_make_witness()]
        receipts = export_episode_provenance(events, witnesses)
        for r in receipts:
            json.dumps(r)  # Should not raise


# ---------------------------------------------------------------------------
# Cross-validation: receipts survive Assay proof pack verification
# ---------------------------------------------------------------------------

_ASSAY_SRC = Path.home() / "assay-toolkit" / "src"
if _ASSAY_SRC.exists() and str(_ASSAY_SRC) not in sys.path:
    sys.path.insert(0, str(_ASSAY_SRC))

try:
    from assay.integrity import verify_receipt_pack
    HAS_ASSAY_VERIFIER = True
except ImportError:
    HAS_ASSAY_VERIFIER = False


@pytest.mark.skipif(not HAS_ASSAY_VERIFIER, reason="assay-toolkit not available")
class TestAssayCrossValidation:
    """Prove AgentMesh provenance receipts pass Assay integrity verification."""

    def _mixed_receipt_pack(self) -> List[Dict]:
        """Build a receipt pack mixing app receipts + AgentMesh provenance."""
        # Simulate normal app receipts
        app_receipts = [
            {
                "receipt_id": "r_app_001",
                "type": "model_call",
                "timestamp": "2026-03-16T11:59:00.000000+00:00",
                "schema_version": "3.0",
                "seq": 0,
                "model_id": "gpt-4o",
                "tokens_in": 100,
                "tokens_out": 50,
            },
            {
                "receipt_id": "r_app_002",
                "type": "guardian_verdict",
                "timestamp": "2026-03-16T11:59:01.000000+00:00",
                "schema_version": "3.0",
                "seq": 1,
                "verdict": "allow",
            },
        ]

        # Add AgentMesh provenance
        weave_events = [
            _make_weave_event(event_id="weave_001", sequence_id=1),
            _make_weave_event(
                event_id="weave_002",
                sequence_id=2,
                prev_hash="sha256:38f92b23bcb51c9bf5231f7336c508a2ba4cf788",
                event_hash="sha256:aaaa2b23bcb51c9bf5231f7336c508a2ba4cf788",
            ),
        ]
        witnesses = [_make_witness()]

        provenance = export_episode_provenance(weave_events, witnesses, seq_offset=1000)
        return app_receipts + provenance

    def test_mixed_pack_passes_receipt_verification(self):
        """Receipt pack with app + AgentMesh receipts passes integrity check."""
        pack = self._mixed_receipt_pack()
        result = verify_receipt_pack(pack)
        assert result.passed, f"Verification failed: {result.errors}"

    def test_provenance_receipts_individually_valid(self):
        """Each provenance receipt passes individual verification."""
        weave_events = [_make_weave_event()]
        witnesses = [_make_witness()]
        provenance = export_episode_provenance(weave_events, witnesses)

        # Verify each individually (as a single-receipt pack)
        for r in provenance:
            result = verify_receipt_pack([r])
            assert result.passed, (
                f"Receipt {r['receipt_id']} failed: {result.errors}"
            )

    def test_no_duplicate_receipt_ids(self):
        """Exported receipts have unique receipt_ids."""
        events = [
            _make_weave_event(event_id="weave_001", sequence_id=1),
            _make_weave_event(event_id="weave_002", sequence_id=2),
        ]
        witnesses = [_make_witness(), _make_witness()]
        receipts = export_episode_provenance(events, witnesses)
        ids = [r["receipt_id"] for r in receipts]
        assert len(ids) == len(set(ids)), f"Duplicate receipt_ids: {ids}"
