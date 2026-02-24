"""AgentMesh CLI -- local-first multi-agent coordination."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from . import __version__
from .models import Agent, AgentKind, AgentStatus, EventKind, _now
from . import db, events

app = typer.Typer(name="agentmesh", help="Local-first multi-agent coordination substrate.")
console = Console()

_DATA_DIR: Path | None = None


def _get_data_dir() -> Path | None:
    env = os.environ.get("AGENTMESH_DATA_DIR")
    if env:
        return Path(env)
    return _DATA_DIR


def _auto_agent_id() -> str:
    env_id = os.environ.get("AGENTMESH_AGENT_ID")
    if env_id:
        return env_id
    tty = os.environ.get("TTY", "")
    if not tty:
        try:
            tty = os.ttyname(0)
        except OSError:
            tty = "notty"
    tty_base = Path(tty).name if tty else "notty"
    return f"claude_{tty_base}_{os.getpid()}"


def _ensure_db() -> None:
    db.init_db(_get_data_dir())


@app.callback()
def main(
    data_dir: Optional[str] = typer.Option(None, "--data-dir", envvar="AGENTMESH_DATA_DIR",
                                           help="Override data directory"),
    version: bool = typer.Option(False, "--version", "-V", help="Show version"),
) -> None:
    global _DATA_DIR
    if data_dir:
        _DATA_DIR = Path(data_dir)
    if version:
        console.print(f"agentmesh {__version__}")
        raise typer.Exit()


# -- Agent commands --

@app.command()
def register(
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="Agent ID (auto-detected if omitted)"),
    kind: str = typer.Option("claude_code", "--kind", "-k"),
    name: str = typer.Option("", "--name", "-n", help="Friendly display name"),
) -> None:
    """Register an agent in the mesh."""
    _ensure_db()
    agent_id = agent or _auto_agent_id()
    agent_kind = AgentKind(kind)
    now = _now()
    a = Agent(
        agent_id=agent_id, kind=agent_kind, display_name=name or agent_id,
        cwd=os.getcwd(), pid=os.getpid(), tty=os.environ.get("TTY", ""),
        status=AgentStatus.IDLE, registered_at=now, last_heartbeat=now,
    )
    db.register_agent(a, _get_data_dir())
    events.append_event(
        EventKind.REGISTER, agent_id=agent_id,
        payload={"kind": kind, "name": name, "cwd": os.getcwd()},
        data_dir=_get_data_dir(),
    )
    console.print(f"Registered [bold]{agent_id}[/bold]")


@app.command()
def deregister(
    agent: Optional[str] = typer.Option(None, "--agent", "-a"),
) -> None:
    """Mark an agent as gone."""
    _ensure_db()
    agent_id = agent or _auto_agent_id()
    ok = db.deregister_agent(agent_id, _get_data_dir())
    if ok:
        events.append_event(EventKind.DEREGISTER, agent_id=agent_id, data_dir=_get_data_dir())
        console.print(f"Deregistered [bold]{agent_id}[/bold]")
    else:
        console.print(f"Agent [bold]{agent_id}[/bold] not found", style="red")
        raise typer.Exit(1)


@app.command()
def heartbeat(
    agent: Optional[str] = typer.Option(None, "--agent", "-a"),
    status: str = typer.Option("busy", "--status", "-s"),
) -> None:
    """Update agent heartbeat and status."""
    _ensure_db()
    agent_id = agent or _auto_agent_id()
    agent_status = AgentStatus(status)
    ok = db.update_heartbeat(agent_id, agent_status, data_dir=_get_data_dir())
    if ok:
        events.append_event(
            EventKind.HEARTBEAT, agent_id=agent_id,
            payload={"status": status}, data_dir=_get_data_dir(),
        )
    else:
        console.print(f"Agent [bold]{agent_id}[/bold] not found", style="red")
        raise typer.Exit(1)
