"""Message formatting and channel helpers."""

from __future__ import annotations

import uuid
from pathlib import Path

from .models import EventKind, Message, Severity, _now
from . import db, events

_SEVERITY_STYLES = {
    Severity.FYI: "dim",
    Severity.ATTN: "yellow",
    Severity.BLOCKER: "red bold",
    Severity.HANDOFF: "cyan bold",
}


def post(
    from_agent: str,
    body: str,
    to_agent: str | None = None,
    channel: str = "general",
    severity: Severity = Severity.FYI,
    episode_id: str | None = None,
    data_dir: Path | None = None,
) -> Message:
    """Post a message to the board. Auto-tags with current episode if episode_id is None."""
    if episode_id is None:
        from .episodes import get_current_episode
        episode_id = get_current_episode(data_dir)

    msg_id = f"msg_{uuid.uuid4().hex[:12]}"
    msg = Message(
        msg_id=msg_id, from_agent=from_agent, to_agent=to_agent,
        channel=channel, severity=severity, body=body,
        created_at=_now(), episode_id=episode_id,
    )
    db.post_message(msg, data_dir)
    events.append_event(
        EventKind.MSG, agent_id=from_agent,
        payload={"msg_id": msg_id, "to": to_agent, "severity": severity.value,
                 "channel": channel, "episode_id": episode_id},
        data_dir=data_dir,
    )
    return msg


def inbox(
    agent_id: str | None = None,
    unread: bool = False,
    channel: str | None = None,
    severity: Severity | None = None,
    limit: int = 20,
    data_dir: Path | None = None,
) -> list[Message]:
    """Get messages, optionally filtered."""
    return db.list_messages(
        data_dir=data_dir, channel=channel, severity=severity,
        to_agent=agent_id, unread_by=agent_id if unread else None,
        limit=limit,
    )


def severity_style(sev: Severity) -> str:
    return _SEVERITY_STYLES.get(sev, "")
