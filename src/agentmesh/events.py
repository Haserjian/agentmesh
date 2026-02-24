"""Append-only JSONL event log with hash chaining."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .models import Event, EventKind, _now

_DEFAULT_DIR = Path.home() / ".agentmesh"
_GENESIS_HASH = "sha256:0000000000000000000000000000000000000000000000000000000000000000"


def _event_path(data_dir: Path | None = None) -> Path:
    d = data_dir or _DEFAULT_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d / "events.jsonl"


def _hash_event(data: dict[str, Any]) -> str:
    """SHA-256 hash of the canonical JSON representation (sorted keys, no event_hash)."""
    d = {k: v for k, v in data.items() if k != "event_hash"}
    canonical = json.dumps(d, sort_keys=True, separators=(",", ":"))
    h = hashlib.sha256(canonical.encode()).hexdigest()
    return f"sha256:{h}"


def _read_last_event(path: Path) -> tuple[int, str]:
    """Read last seq and hash from the event log. Returns (0, genesis) if empty."""
    if not path.exists() or path.stat().st_size == 0:
        return 0, _GENESIS_HASH
    # Read last non-empty line
    with open(path, "r") as f:
        last_line = ""
        for line in f:
            stripped = line.strip()
            if stripped:
                last_line = stripped
    if not last_line:
        return 0, _GENESIS_HASH
    last = json.loads(last_line)
    return last["seq"], last["event_hash"]


def append_event(
    kind: EventKind,
    agent_id: str = "",
    payload: dict[str, Any] | None = None,
    data_dir: Path | None = None,
) -> Event:
    """Append a new event to the JSONL log. Process-safe via flock + O_APPEND."""
    path = _event_path(data_dir)

    # Open with O_APPEND for atomic appends
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            # Read current state while holding lock
            seq, prev_hash = _read_last_event(path)
            new_seq = seq + 1
            event_id = f"evt_{new_seq:06d}"

            data = {
                "event_id": event_id,
                "seq": new_seq,
                "ts": _now(),
                "kind": kind.value,
                "agent_id": agent_id,
                "payload": payload or {},
                "prev_hash": prev_hash,
            }
            data["event_hash"] = _hash_event(data)

            line = json.dumps(data, separators=(",", ":")) + "\n"
            os.write(fd, line.encode())

            return Event(**data)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def read_events(data_dir: Path | None = None, since_seq: int = 0) -> list[Event]:
    """Read events from the log, optionally starting from a sequence number."""
    path = _event_path(data_dir)
    if not path.exists():
        return []
    events = []
    with open(path, "r") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            data = json.loads(stripped)
            if data["seq"] > since_seq:
                events.append(Event(**data))
    return events


def verify_chain(data_dir: Path | None = None) -> tuple[bool, str]:
    """Verify the hash chain integrity. Returns (valid, error_message)."""
    path = _event_path(data_dir)
    if not path.exists():
        return True, ""

    prev_hash = _GENESIS_HASH
    with open(path, "r") as f:
        for i, line in enumerate(f, 1):
            stripped = line.strip()
            if not stripped:
                continue
            data = json.loads(stripped)
            if data["prev_hash"] != prev_hash:
                return False, f"Chain break at seq {data['seq']}: expected prev_hash {prev_hash}"
            computed = _hash_event(data)
            if data["event_hash"] != computed:
                return False, f"Hash mismatch at seq {data['seq']}: stored={data['event_hash']} computed={computed}"
            prev_hash = data["event_hash"]
    return True, ""


def gc_events(max_age_hours: int = 72, data_dir: Path | None = None) -> int:
    """Remove events older than max_age_hours. Returns count removed.

    Rewrites the file (re-chains hashes) while holding flock.
    """
    from datetime import datetime, timedelta, timezone
    path = _event_path(data_dir)
    if not path.exists():
        return 0

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()

    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            all_events = read_events(data_dir)
            keep = [e for e in all_events if e.ts >= cutoff]
            removed = len(all_events) - len(keep)
            if removed == 0:
                return 0

            # Rewrite with new hash chain
            prev_hash = _GENESIS_HASH
            lines = []
            for i, evt in enumerate(keep, 1):
                data = {
                    "event_id": evt.event_id,
                    "seq": i,
                    "ts": evt.ts,
                    "kind": evt.kind if isinstance(evt.kind, str) else evt.kind.value,
                    "agent_id": evt.agent_id,
                    "payload": evt.payload,
                    "prev_hash": prev_hash,
                }
                data["event_hash"] = _hash_event(data)
                prev_hash = data["event_hash"]
                lines.append(json.dumps(data, separators=(",", ":")) + "\n")

            # Truncate and rewrite
            with open(path, "w") as f:
                f.writelines(lines)

            return removed
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
