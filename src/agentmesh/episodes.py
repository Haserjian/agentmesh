"""Episode lifecycle -- stable episode_id binding across claims, capsules, messages."""

from __future__ import annotations

import os
import random
import struct
import time
from pathlib import Path

from .models import _now

_DEFAULT_DIR = Path.home() / ".agentmesh"


def generate_episode_id() -> str:
    """Generate a ULID-like episode ID: ep_ + 48-bit ms timestamp + 48-bit random.

    Lexicographically sortable, 27 chars total.
    """
    ms = int(time.time() * 1000)
    ts_bytes = struct.pack(">Q", ms)[2:]  # 6 bytes = 48 bits
    rand_bytes = random.getrandbits(48).to_bytes(6, "big")
    raw = ts_bytes + rand_bytes
    return "ep_" + raw.hex()


def start_episode(
    title: str = "",
    parent_episode_id: str = "",
    data_dir: Path | None = None,
) -> str:
    """Start a new episode and set it as current. Returns episode_id."""
    from . import db

    episode_id = generate_episode_id()
    now = _now()
    db.create_episode(
        episode_id=episode_id,
        title=title,
        started_at=now,
        parent_episode_id=parent_episode_id,
        data_dir=data_dir,
    )
    set_current_episode(episode_id, data_dir)
    return episode_id


def get_current_episode(data_dir: Path | None = None) -> str:
    """Read the current episode ID from the current_episode file. Returns '' if none."""
    d = data_dir or _DEFAULT_DIR
    path = d / "current_episode"
    if not path.exists():
        return ""
    return path.read_text().strip()


def set_current_episode(episode_id: str, data_dir: Path | None = None) -> None:
    """Write the current episode ID."""
    d = data_dir or _DEFAULT_DIR
    d.mkdir(parents=True, exist_ok=True)
    path = d / "current_episode"
    path.write_text(episode_id)


def end_episode(data_dir: Path | None = None) -> str:
    """End the current episode. Returns the ended episode_id, or '' if none."""
    from . import db

    episode_id = get_current_episode(data_dir)
    if not episode_id:
        return ""
    db.end_episode(episode_id, ended_at=_now(), data_dir=data_dir)
    d = data_dir or _DEFAULT_DIR
    path = d / "current_episode"
    if path.exists():
        path.unlink()
    return episode_id
