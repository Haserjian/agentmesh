"""Provenance export — convert AgentMesh evidence into Assay receipt dicts.

AM-ASSAY-001: First bridge from AgentMesh provenance into Assay proof packs.

Exports weave events and witness signatures as receipt-shaped dicts that
flow natively into Assay's receipt_pack.jsonl. Assay proof packs are
type-agnostic — any dict with receipt_id, type, timestamp gets included
and verified without schema enforcement.

This module does NOT import assay-toolkit. It produces plain dicts.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from .models import WeaveEvent


def weave_event_to_receipt(
    event: WeaveEvent,
    *,
    seq_offset: int = 0,
) -> Dict[str, Any]:
    """Convert a single WeaveEvent into an Assay-compatible receipt dict.

    Args:
        event: WeaveEvent from the hash-chained provenance ledger.
        seq_offset: Offset to add to sequence_id for pack ordering.

    Returns:
        Dict with receipt_id, type, timestamp and provenance fields.
    """
    return {
        # Assay required fields
        "receipt_id": f"amesh_{event.event_id}",
        "type": "agentmesh_weave",
        "timestamp": event.created_at,
        "schema_version": "1.0",
        "seq": event.sequence_id + seq_offset,

        # Provenance fields (carry the hash chain)
        "event_id": event.event_id,
        "episode_id": event.episode_id,
        "sequence_id": event.sequence_id,
        "prev_hash": event.prev_hash,
        "event_hash": event.event_hash,

        # Git binding
        "git_commit_sha": event.git_commit_sha,
        "git_patch_hash": event.git_patch_hash,
        "affected_files": event.affected_symbols,

        # Lineage
        "capsule_id": event.capsule_id,
        "trace_id": event.trace_id,
    }


def _witness_content_hash(witness: Dict[str, Any]) -> str:
    """Derive a deterministic 24-char hex ID from stable witness content.

    Same witness payload always produces the same receipt_id, ensuring
    identical provenance exports yield byte-identical receipt objects.
    """
    canonical = json.dumps(witness, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def witness_to_receipt(
    witness: Dict[str, Any],
    *,
    seq: int = 0,
) -> Dict[str, Any]:
    """Convert a witness dict (cwe_v1) into an Assay-compatible receipt dict.

    Args:
        witness: Witness payload from ~/.agentmesh/witnesses/ or commit trailers.
        seq: Sequence number for pack ordering.

    Returns:
        Dict with receipt_id, type, timestamp and witness fields.
    """
    return {
        # Assay required fields
        "receipt_id": f"amesh_witness_{_witness_content_hash(witness)}",
        "type": "agentmesh_witness",
        "timestamp": witness.get("timestamp", ""),
        "schema_version": "1.0",
        "seq": seq,

        # Witness fields
        "witness_schema": witness.get("schema_version", "cwe_v1"),
        "episode_id": witness.get("episode_id", ""),
        "agent_id": witness.get("agent_id", ""),
        "patch_id_stable": witness.get("patch_id_stable", ""),
        "patch_hash_verbatim": witness.get("patch_hash_verbatim", ""),
        "files_count": witness.get("files_count", 0),
        "files_hash": witness.get("files_hash", ""),

        # Signer identity (public key travels with the receipt)
        "signer_algorithm": witness.get("signer", {}).get("algorithm", ""),
        "signer_key_id": witness.get("signer", {}).get("key_id", ""),
        "signer_public_key": witness.get("signer", {}).get("public_key", ""),
    }


def export_episode_provenance(
    weave_events: List[WeaveEvent],
    witnesses: Optional[List[Dict[str, Any]]] = None,
    *,
    seq_offset: int = 1000,
) -> List[Dict[str, Any]]:
    """Export all provenance for an episode as Assay-compatible receipt dicts.

    Weave events carry the hash-chained provenance (what happened, in order,
    with git bindings). Witness receipts carry Ed25519 signatures proving
    who did the work.

    Args:
        weave_events: WeaveEvents for the episode (from db.list_weave_events).
        witnesses: Optional witness dicts (from ~/.agentmesh/witnesses/).
        seq_offset: Offset for seq numbers to sort after app receipts in pack.

    Returns:
        List of receipt dicts ready for Assay's receipt_pack.jsonl.
    """
    receipts: List[Dict[str, Any]] = []

    for event in weave_events:
        receipts.append(weave_event_to_receipt(event, seq_offset=seq_offset))

    for i, witness in enumerate(witnesses or []):
        receipts.append(witness_to_receipt(
            witness,
            seq=seq_offset + len(weave_events) + i,
        ))

    return receipts


__all__ = [
    "weave_event_to_receipt",
    "witness_to_receipt",
    "export_episode_provenance",
]
