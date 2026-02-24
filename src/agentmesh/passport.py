"""Thread Passport (.meshpack) -- portable signed episode bundle."""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import tarfile
from pathlib import Path
from typing import Any

from . import db
from .models import _now


def _jsonl(items: list[dict[str, Any]]) -> str:
    """Serialize a list of dicts as JSONL."""
    return "".join(json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n" for item in items)


def _hmac_sha256(key: str, data: bytes) -> str:
    return hmac.new(key.encode(), data, hashlib.sha256).hexdigest()


def _add_to_tar(tf: tarfile.TarFile, name: str, content: bytes) -> None:
    """Add a file to a tar archive with deterministic metadata."""
    info = tarfile.TarInfo(name=name)
    info.size = len(content)
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mode = 0o644
    tf.addfile(info, io.BytesIO(content))


def export_episode(
    episode_id: str,
    output_path: Path | None = None,
    data_dir: Path | None = None,
) -> Path:
    """Export an episode as a .meshpack (deterministic tar.gz with HMAC signature).

    Returns the output path.
    """
    ep = db.get_episode(episode_id, data_dir)
    if ep is None:
        raise ValueError(f"Episode {episode_id} not found")

    # Query episode-scoped data
    conn = db.get_connection(data_dir)
    try:
        capsules_rows = conn.execute(
            "SELECT * FROM capsules WHERE episode_id = ? ORDER BY created_at",
            (episode_id,),
        ).fetchall()
        claims_rows = conn.execute(
            "SELECT * FROM claims WHERE episode_id = ? ORDER BY created_at",
            (episode_id,),
        ).fetchall()
        messages_rows = conn.execute(
            "SELECT * FROM messages WHERE episode_id = ? ORDER BY created_at",
            (episode_id,),
        ).fetchall()
        weave_rows = conn.execute(
            "SELECT * FROM weave_events WHERE episode_id = ? ORDER BY created_at",
            (episode_id,),
        ).fetchall()
    finally:
        conn.close()

    capsules_data = [dict(r) for r in capsules_rows]
    claims_data = [dict(r) for r in claims_rows]
    messages_data = [dict(r) for r in messages_rows]
    weave_data = [dict(r) for r in weave_rows]

    capsules_jsonl = _jsonl(capsules_data).encode()
    claims_jsonl = _jsonl(claims_data).encode()
    messages_jsonl = _jsonl(messages_data).encode()
    weave_jsonl = _jsonl(weave_data).encode()

    # Build manifest
    manifest = {
        "episode_id": episode_id,
        "title": ep.title,
        "started_at": ep.started_at,
        "ended_at": ep.ended_at,
        "parent_episode_id": ep.parent_episode_id,
        "exported_at": _now(),
        "counts": {
            "capsules": len(capsules_data),
            "claims": len(claims_data),
            "messages": len(messages_data),
            "weave_events": len(weave_data),
        },
    }

    # HMAC signature (key = episode_id)
    payload = capsules_jsonl + claims_jsonl + messages_jsonl + weave_jsonl
    manifest["signature"] = _hmac_sha256(episode_id, payload)

    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode()

    # Write deterministic tar.gz
    if output_path is None:
        output_path = Path(f"{episode_id}.meshpack")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        _add_to_tar(tf, "manifest.json", manifest_bytes)
        _add_to_tar(tf, "capsules.jsonl", capsules_jsonl)
        _add_to_tar(tf, "claims_snapshot.jsonl", claims_jsonl)
        _add_to_tar(tf, "messages.jsonl", messages_jsonl)
        _add_to_tar(tf, "weave_slice.jsonl", weave_jsonl)

    output_path.write_bytes(buf.getvalue())
    return output_path


def verify_meshpack(pack_path: Path) -> tuple[bool, dict[str, Any]]:
    """Verify a .meshpack signature. Returns (valid, manifest_dict)."""
    with tarfile.open(str(pack_path), "r:gz") as tf:
        manifest_bytes = tf.extractfile("manifest.json").read()  # type: ignore
        manifest = json.loads(manifest_bytes)

        capsules = tf.extractfile("capsules.jsonl").read()  # type: ignore
        claims = tf.extractfile("claims_snapshot.jsonl").read()  # type: ignore
        messages = tf.extractfile("messages.jsonl").read()  # type: ignore
        weave = tf.extractfile("weave_slice.jsonl").read()  # type: ignore

    payload = capsules + claims + messages + weave
    expected_sig = manifest.get("signature", "")
    episode_id = manifest.get("episode_id", "")
    computed_sig = _hmac_sha256(episode_id, payload)

    return expected_sig == computed_sig, manifest


def import_meshpack(
    pack_path: Path,
    namespace: str = "",
    data_dir: Path | None = None,
) -> dict[str, int]:
    """Import a .meshpack into the local DB. Returns counts of imported items.

    Verifies signature first. If namespace is provided, prefixes episode_id.
    """
    valid, manifest = verify_meshpack(pack_path)
    if not valid:
        raise ValueError("meshpack signature verification failed")

    episode_id = manifest["episode_id"]
    if namespace:
        episode_id = f"{namespace}/{episode_id}"

    # Import episode
    db.create_episode(
        episode_id=episode_id,
        title=manifest.get("title", ""),
        started_at=manifest.get("started_at", ""),
        parent_episode_id=manifest.get("parent_episode_id", ""),
        data_dir=data_dir,
    )
    if manifest.get("ended_at"):
        db.end_episode(episode_id, ended_at=manifest["ended_at"], data_dir=data_dir)

    counts = {"episodes": 1, "capsules": 0, "claims": 0, "messages": 0, "weave_events": 0}

    with tarfile.open(str(pack_path), "r:gz") as tf:
        # Import capsules
        raw = tf.extractfile("capsules.jsonl").read().decode()  # type: ignore
        for line in raw.strip().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            row["episode_id"] = episode_id
            capsule = db._row_to_capsule_from_dict(row)
            db.save_capsule(capsule, data_dir)
            counts["capsules"] += 1

        # Import messages
        raw = tf.extractfile("messages.jsonl").read().decode()  # type: ignore
        for line in raw.strip().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            row["episode_id"] = episode_id
            msg = db._row_to_message_from_dict(row)
            db.post_message(msg, data_dir)
            counts["messages"] += 1

        # Import weave events
        raw = tf.extractfile("weave_slice.jsonl").read().decode()  # type: ignore
        for line in raw.strip().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            row["episode_id"] = episode_id
            from .models import WeaveEvent
            evt = WeaveEvent(
                event_id=row["event_id"],
                episode_id=episode_id,
                prev_hash=row.get("prev_hash", ""),
                capsule_id=row.get("capsule_id", ""),
                git_commit_sha=row.get("git_commit_sha", ""),
                git_patch_hash=row.get("git_patch_hash", ""),
                affected_symbols=json.loads(row["affected_symbols"]) if isinstance(row["affected_symbols"], str) else row.get("affected_symbols", []),
                trace_id=row.get("trace_id", ""),
                parent_event_id=row.get("parent_event_id", ""),
                event_hash=row.get("event_hash", ""),
                created_at=row.get("created_at", ""),
            )
            db.save_weave_event(evt, data_dir)
            counts["weave_events"] += 1

    return counts
