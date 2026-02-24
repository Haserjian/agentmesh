"""Provenance Weaver -- append-only intent ledger linking capsules to git commits."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from .models import WeaveEvent, _now
from . import db
from .episodes import get_current_episode


def _hash_weave(data: dict[str, Any]) -> str:
    """SHA-256 of canonical JSON (sorted keys, no event_hash field)."""
    d = {k: v for k, v in data.items() if k != "event_hash"}
    canonical = json.dumps(d, sort_keys=True, separators=(",", ":"))
    h = hashlib.sha256(canonical.encode()).hexdigest()
    return f"sha256:{h}"


def append_weave(
    capsule_id: str = "",
    git_commit_sha: str = "",
    git_patch_hash: str = "",
    affected_symbols: list[str] | None = None,
    trace_id: str = "",
    parent_event_id: str = "",
    episode_id: str | None = None,
    data_dir: Path | None = None,
) -> WeaveEvent:
    """Append a weave event to the provenance ledger.

    Auto-tags with current episode if episode_id is None.
    Returns the created WeaveEvent.
    """
    if episode_id is None:
        episode_id = get_current_episode(data_dir)

    prev_hash = db.get_last_weave_hash(data_dir)
    event_id = f"weave_{uuid.uuid4().hex[:12]}"
    now = _now()

    data = {
        "event_id": event_id,
        "episode_id": episode_id,
        "prev_hash": prev_hash,
        "capsule_id": capsule_id,
        "git_commit_sha": git_commit_sha,
        "git_patch_hash": git_patch_hash,
        "affected_symbols": affected_symbols or [],
        "trace_id": trace_id,
        "parent_event_id": parent_event_id,
        "created_at": now,
    }
    data["event_hash"] = _hash_weave(data)

    event = WeaveEvent(**data)
    db.save_weave_event(event, data_dir)
    return event


def verify_weave(data_dir: Path | None = None) -> tuple[bool, str]:
    """Verify the weave hash chain. Returns (valid, error_msg)."""
    events = db.list_weave_events(data_dir)
    if not events:
        return True, ""

    genesis = "sha256:" + "0" * 64
    prev_hash = genesis

    for evt in events:
        if evt.prev_hash != prev_hash:
            return False, (
                f"Chain break at {evt.event_id}: "
                f"expected prev_hash {prev_hash}, got {evt.prev_hash}"
            )
        data = {
            "event_id": evt.event_id,
            "episode_id": evt.episode_id,
            "prev_hash": evt.prev_hash,
            "capsule_id": evt.capsule_id,
            "git_commit_sha": evt.git_commit_sha,
            "git_patch_hash": evt.git_patch_hash,
            "affected_symbols": evt.affected_symbols,
            "trace_id": evt.trace_id,
            "parent_event_id": evt.parent_event_id,
            "created_at": evt.created_at,
        }
        computed = _hash_weave(data)
        if evt.event_hash != computed:
            return False, (
                f"Hash mismatch at {evt.event_id}: "
                f"stored={evt.event_hash} computed={computed}"
            )
        prev_hash = evt.event_hash

    return True, ""


def trace_file(
    path: str,
    at_event_id: str | None = None,
    data_dir: Path | None = None,
) -> list[WeaveEvent]:
    """Find weave events affecting a given file path."""
    events = db.list_weave_events(data_dir)
    matches = []
    for evt in events:
        if at_event_id and evt.event_id == at_event_id:
            # Include this one and stop
            for sym in evt.affected_symbols:
                if path in sym:
                    matches.append(evt)
                    break
            break
        for sym in evt.affected_symbols:
            if path in sym:
                matches.append(evt)
                break
    return matches


def export_weave_md(
    episode_id: str | None = None,
    data_dir: Path | None = None,
) -> str:
    """Export weave events as a Markdown provenance summary."""
    events = db.list_weave_events(data_dir, episode_id=episode_id)
    if not events:
        return "# Provenance Weave\n\nNo events recorded.\n"

    lines = ["# Provenance Weave\n"]
    if episode_id:
        lines.append(f"Episode: `{episode_id}`\n")
    lines.append(f"Events: {len(events)}\n")
    lines.append("")

    for evt in events:
        lines.append(f"## {evt.event_id}")
        lines.append(f"- **created**: {evt.created_at}")
        if evt.capsule_id:
            lines.append(f"- **capsule**: {evt.capsule_id}")
        if evt.git_commit_sha:
            lines.append(f"- **commit**: {evt.git_commit_sha}")
        if evt.affected_symbols:
            lines.append(f"- **symbols**: {', '.join(evt.affected_symbols)}")
        lines.append(f"- **hash**: `{evt.event_hash[:20]}...`")
        lines.append("")

    # Files Changed summary table (aggregate affected_symbols by file)
    file_commits: dict[str, list[str]] = {}
    for evt in events:
        sha = evt.git_commit_sha
        if not sha:
            continue
        for sym in evt.affected_symbols:
            file_commits.setdefault(sym, []).append(sha[:8])
    if file_commits:
        lines.append("## Files Changed\n")
        lines.append("| File | Commits |")
        lines.append("|------|---------|")
        for fpath in sorted(file_commits):
            shas = ", ".join(file_commits[fpath])
            lines.append(f"| `{fpath}` | {shas} |")
        lines.append("")

    return "\n".join(lines)
