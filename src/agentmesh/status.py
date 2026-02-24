"""Rich dashboard for mesh status."""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.text import Text

from . import db
from .messages import severity_style
from .models import Severity


def render_status(data_dir: Path | None = None, console: Console | None = None,
                  as_json: bool = False) -> str | None:
    """Render the full status dashboard. Returns JSON string if as_json=True."""
    c = console or Console()

    agents = db.list_agents(data_dir)
    all_claims = db.list_claims(data_dir, active_only=True)
    msgs = db.list_messages(data_dir, limit=10)
    capsules = db.list_capsules(data_dir, limit=5)

    if as_json:
        return json.dumps({
            "agents": [a.model_dump() for a in agents],
            "claims": [cl.model_dump() for cl in all_claims],
            "messages": [m.model_dump() for m in msgs],
            "capsules": [cap.model_dump() for cap in capsules],
        }, indent=2)

    # Agents table
    at = Table(title="Agents", show_header=True, header_style="bold")
    at.add_column("ID")
    at.add_column("Kind")
    at.add_column("Status")
    at.add_column("Name")
    at.add_column("CWD")
    for a in agents:
        style = "green" if a.status.value == "idle" else "yellow" if a.status.value == "busy" else "red"
        at.add_row(a.agent_id, a.kind.value, f"[{style}]{a.status.value}[/]", a.display_name, a.cwd)

    # Claims table
    ct = Table(title="Active Claims", show_header=True, header_style="bold")
    ct.add_column("Agent")
    ct.add_column("Path")
    ct.add_column("Intent")
    ct.add_column("Expires")
    for cl in all_claims:
        ct.add_row(cl.agent_id, cl.path, cl.intent.value, cl.expires_at[:19])

    # Messages
    mt = Table(title="Recent Messages", show_header=True, header_style="bold")
    mt.add_column("From")
    mt.add_column("Sev")
    mt.add_column("Body")
    mt.add_column("Time")
    for m in msgs[:10]:
        style = severity_style(m.severity)
        mt.add_row(m.from_agent, f"[{style}]{m.severity.value}[/]", m.body[:60], m.created_at[:19])

    c.print()
    c.print(at)
    c.print()
    c.print(ct)
    c.print()
    c.print(mt)

    if capsules:
        c.print()
        c.print(f"[dim]{len(capsules)} recent capsule(s)[/dim]")

    return None
