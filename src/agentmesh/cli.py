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
from .models import Agent, AgentKind, AgentStatus, ClaimIntent, EventKind, Severity, _now
from . import db, events, claims, messages, status, capsules, episodes

app = typer.Typer(name="agentmesh", help="Local-first multi-agent coordination substrate.")
console = Console()

_DATA_DIR: Path | None = None


def _get_data_dir() -> Path | None:
    env = os.environ.get("AGENTMESH_DATA_DIR")
    if env:
        return Path(env)
    return _DATA_DIR


def _auto_agent_id() -> str:
    """Session-stable agent ID: env var > TTY-based > fallback.

    Does NOT include PID, so the ID is stable across CLI invocations
    within the same terminal session.
    """
    env_id = os.environ.get("AGENTMESH_AGENT_ID")
    if env_id:
        return env_id
    tty = ""
    try:
        tty = os.ttyname(0)
    except OSError:
        tty = os.environ.get("TTY", "")
    if tty:
        tty_base = Path(tty).name
        return f"claude_{tty_base}"
    # No TTY (e.g. piped/cron): use a persistent session file per $HOME
    session_file = Path.home() / ".agentmesh" / ".session_id"
    if session_file.exists():
        return session_file.read_text().strip()
    session_file.parent.mkdir(parents=True, exist_ok=True)
    import uuid as _uuid
    sid = f"claude_{_uuid.uuid4().hex[:8]}"
    session_file.write_text(sid)
    return sid


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


# -- Claim commands --

@app.command()
def claim(
    resources: list[str] = typer.Argument(..., help="Resources to claim (paths, PORT:N, LOCK:name, TEST_SUITE:name, TEMP_DIR:path)"),
    agent: Optional[str] = typer.Option(None, "--agent", "-a"),
    ttl: int = typer.Option(1800, "--ttl", "-t", help="TTL in seconds"),
    intent: str = typer.Option("edit", "--intent", "-i"),
    reason: str = typer.Option("", "--reason", "-r"),
    force: bool = typer.Option(False, "--force", "-f", help="Override existing claims"),
) -> None:
    """Claim resources for editing. Supports file paths and typed resources (PORT:3000, LOCK:npm)."""
    _ensure_db()
    agent_id = agent or _auto_agent_id()
    claim_intent = ClaimIntent(intent)
    had_conflict = False
    for p in resources:
        ok, clm, conflicts = claims.make_claim(
            agent_id, p, intent=claim_intent, ttl_s=ttl,
            reason=reason, force=force, data_dir=_get_data_dir(),
        )
        if ok:
            rt_label = clm.resource_type.value.upper() if clm.resource_type.value != "file" else ""
            prefix = f"[{rt_label}] " if rt_label else ""
            console.print(f"Claimed {prefix}[bold]{clm.path}[/bold] (ttl={ttl}s)")
            if conflicts:
                console.print(f"  (forced over {len(conflicts)} existing claim(s))", style="yellow")
        else:
            had_conflict = True
            console.print(f"CONFLICT on [bold]{p}[/bold]:", style="red bold")
            console.print(claims.format_conflict(conflicts))
    if had_conflict:
        raise typer.Exit(1)


@app.command()
def release(
    paths: list[str] = typer.Argument(None, help="Paths to release"),
    agent: Optional[str] = typer.Option(None, "--agent", "-a"),
    all_claims: bool = typer.Option(False, "--all", help="Release all claims"),
) -> None:
    """Release file claims."""
    _ensure_db()
    agent_id = agent or _auto_agent_id()
    if all_claims:
        count = claims.release(agent_id, release_all=True, data_dir=_get_data_dir())
        console.print(f"Released {count} claim(s)")
    elif paths:
        total = 0
        for p in paths:
            total += claims.release(agent_id, path=p, data_dir=_get_data_dir())
        console.print(f"Released {total} claim(s)")
    else:
        console.print("Specify paths or --all", style="red")
        raise typer.Exit(1)


@app.command()
def check(
    path: str = typer.Argument(..., help="Path to check"),
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="Exclude this agent from check"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Check for conflicts on a path."""
    _ensure_db()
    conflicts = claims.check(path, exclude_agent=agent, data_dir=_get_data_dir())
    if json_out:
        import json as json_mod
        console.print(json_mod.dumps([c.model_dump() for c in conflicts], indent=2))
    elif conflicts:
        console.print(f"CONFLICT on [bold]{path}[/bold]:", style="red bold")
        console.print(claims.format_conflict(conflicts))
        raise typer.Exit(1)
    else:
        console.print(f"No conflicts on [bold]{path}[/bold]", style="green")


@app.command()
def gc(
    dry_run: bool = typer.Option(False, "--dry-run"),
    max_age: int = typer.Option(72, "--max-age", help="Max age in hours"),
) -> None:
    """Garbage-collect old data."""
    _ensure_db()
    if dry_run:
        console.print(f"Would GC data older than {max_age}h (dry run)")
        return
    result = db.gc_old_data(max_age_hours=max_age, data_dir=_get_data_dir())
    events.append_event(EventKind.GC, payload=result, data_dir=_get_data_dir())
    console.print(f"GC: {result['claims']} claims, {result['agents']} agents, {result['messages']} messages")


# -- Message commands --

@app.command()
def msg(
    text: str = typer.Argument(..., help="Message body"),
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="Sending agent"),
    to: Optional[str] = typer.Option(None, "--to", help="Target agent"),
    severity: str = typer.Option("FYI", "--severity", "-s"),
    channel: str = typer.Option("general", "--channel", "-c"),
) -> None:
    """Post a message to the board."""
    _ensure_db()
    agent_id = agent or _auto_agent_id()
    sev = Severity(severity)
    m = messages.post(agent_id, text, to_agent=to, channel=channel, severity=sev, data_dir=_get_data_dir())
    style = messages.severity_style(sev)
    console.print(f"[{style}][{sev.value}][/] {text}")


@app.command(name="inbox")
def inbox_cmd(
    agent: Optional[str] = typer.Option(None, "--agent", "-a"),
    unread: bool = typer.Option(False, "--unread"),
    channel: Optional[str] = typer.Option(None, "--channel", "-c"),
    severity: Optional[str] = typer.Option(None, "--severity", "-s"),
    limit: int = typer.Option(20, "--limit", "-l"),
) -> None:
    """List messages."""
    _ensure_db()
    agent_id = agent or _auto_agent_id()
    sev = Severity(severity) if severity else None
    msgs = messages.inbox(
        agent_id=agent_id, unread=unread, channel=channel,
        severity=sev, limit=limit, data_dir=_get_data_dir(),
    )
    if not msgs:
        console.print("[dim]No messages[/dim]")
        return
    for m in msgs:
        style = messages.severity_style(m.severity)
        prefix = f"[{style}][{m.severity.value}][/]"
        to_str = f" -> {m.to_agent}" if m.to_agent else ""
        console.print(f"{prefix} {m.from_agent}{to_str}: {m.body}  [dim]{m.created_at[:19]}[/dim]")


# -- Status command --

@app.command(name="status")
def status_cmd(
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
    watch: bool = typer.Option(False, "--watch", "-w", help="Live refresh"),
) -> None:
    """Show mesh status dashboard."""
    _ensure_db()
    if json_out:
        result = status.render_status(data_dir=_get_data_dir(), as_json=True)
        console.print(result)
        return
    if watch:
        import time
        try:
            while True:
                console.clear()
                status.render_status(data_dir=_get_data_dir(), console=console)
                time.sleep(2)
        except KeyboardInterrupt:
            pass
    else:
        status.render_status(data_dir=_get_data_dir(), console=console)


# -- Bundle commands --

bundle_app = typer.Typer(help="Context capsule commands.")
app.add_typer(bundle_app, name="bundle")


@bundle_app.command(name="emit")
def bundle_emit(
    agent: Optional[str] = typer.Option(None, "--agent", "-a"),
    task: str = typer.Option("", "--task", "-t", help="Task description"),
) -> None:
    """Emit a context capsule."""
    _ensure_db()
    agent_id = agent or _auto_agent_id()
    cap = capsules.build_capsule(agent_id, task_desc=task, data_dir=_get_data_dir())
    console.print(f"Capsule [bold]{cap.capsule_id}[/bold] created")
    console.print(f"  branch={cap.git_branch} sha={cap.git_sha}")


@bundle_app.command(name="get")
def bundle_get(
    capsule_id: str = typer.Argument(..., help="Capsule ID"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
    sbar: bool = typer.Option(False, "--sbar", help="Show SBAR handoff summary"),
) -> None:
    """Get a context capsule."""
    _ensure_db()
    bundle = capsules.get_capsule_bundle(capsule_id, data_dir=_get_data_dir())
    if bundle is None:
        console.print(f"Capsule [bold]{capsule_id}[/bold] not found", style="red")
        raise typer.Exit(1)
    if json_out:
        console.print(json.dumps(bundle, indent=2))
    elif sbar:
        sbar_data = bundle.get("sbar", {})
        if not sbar_data:
            console.print("No SBAR data in this capsule", style="yellow")
            return
        console.print(f"[bold]SBAR Handoff -- {capsule_id}[/bold]\n")
        sit = sbar_data.get("situation", {})
        console.print(f"[bold cyan]S[/bold cyan]ituation: {sit.get('global_objective', '')}  ({sit.get('git_head', '')})")
        bg = sbar_data.get("background", {})
        n_files = len(bg.get("changed_files", []))
        console.print(f"[bold cyan]B[/bold cyan]ackground: {n_files} file(s) changed")
        for f in bg.get("changed_files", []):
            console.print(f"  {f.get('path', '')}")
        assess = sbar_data.get("assessment", {})
        console.print(f"[bold cyan]A[/bold cyan]ssessment: tests={assess.get('test_status', 'unknown')}, open_claims={len(assess.get('open_claims', []))}")
        rec = sbar_data.get("recommendation", {})
        actions = rec.get("next_actions", [])
        blockers = rec.get("blockers", [])
        console.print(f"[bold cyan]R[/bold cyan]ecommendation: {len(actions)} action(s), {len(blockers)} blocker(s)")
        for a in actions:
            console.print(f"  - {a}")
    else:
        console.print(f"Capsule: [bold]{capsule_id}[/bold]")
        console.print(f"  Agent: {bundle['agent_id']}")
        console.print(f"  Task: {bundle.get('task_desc', '')}")
        git = bundle.get("git", {})
        console.print(f"  Branch: {git.get('branch', '')}  SHA: {git.get('sha', '')}")
        mesh = bundle.get("mesh", {})
        console.print(f"  Claims: {len(mesh.get('open_claims', []))}  Agents: {len(mesh.get('active_agents', []))}")
        if bundle.get("sbar"):
            console.print("  [dim]SBAR available (use --sbar to view)[/dim]")


# -- Episode commands --

episode_app = typer.Typer(help="Episode lifecycle commands.")
app.add_typer(episode_app, name="episode")


@episode_app.command(name="start")
def episode_start(
    title: str = typer.Option("", "--title", "-t", help="Episode title"),
    parent: str = typer.Option("", "--parent", "-p", help="Parent episode ID"),
) -> None:
    """Start a new episode."""
    _ensure_db()
    ep_id = episodes.start_episode(
        title=title, parent_episode_id=parent, data_dir=_get_data_dir(),
    )
    events.append_event(
        EventKind.EPISODE_START,
        payload={"episode_id": ep_id, "title": title},
        data_dir=_get_data_dir(),
    )
    console.print(f"Episode [bold]{ep_id}[/bold] started")
    if title:
        console.print(f"  title={title}")


@episode_app.command(name="current")
def episode_current() -> None:
    """Show current episode."""
    _ensure_db()
    ep_id = episodes.get_current_episode(_get_data_dir())
    if not ep_id:
        console.print("[dim]No active episode[/dim]")
        return
    ep = db.get_episode(ep_id, _get_data_dir())
    if ep:
        console.print(f"Episode [bold]{ep.episode_id}[/bold]")
        if ep.title:
            console.print(f"  title={ep.title}")
        console.print(f"  started={ep.started_at[:19]}")
    else:
        console.print(f"Episode [bold]{ep_id}[/bold] (no DB record)")


@episode_app.command(name="end")
def episode_end() -> None:
    """End the current episode."""
    _ensure_db()
    ep_id = episodes.end_episode(_get_data_dir())
    if not ep_id:
        console.print("[dim]No active episode to end[/dim]")
        return
    events.append_event(
        EventKind.EPISODE_END,
        payload={"episode_id": ep_id},
        data_dir=_get_data_dir(),
    )
    console.print(f"Episode [bold]{ep_id}[/bold] ended")


# -- Hooks commands --

hooks_app = typer.Typer(help="Claude Code hook management.")
app.add_typer(hooks_app, name="hooks")


@hooks_app.command(name="install")
def hooks_install() -> None:
    """Install Claude Code hooks for collision detection."""
    from .hooks.install import install_hooks
    actions = install_hooks()
    for a in actions:
        console.print(f"  {a}")
    console.print("[green]Hooks installed[/green]")


@hooks_app.command(name="uninstall")
def hooks_uninstall() -> None:
    """Remove AgentMesh hooks from Claude Code."""
    from .hooks.install import uninstall_hooks
    actions = uninstall_hooks()
    for a in actions:
        console.print(f"  {a}")
    console.print("[yellow]Hooks uninstalled[/yellow]")


@hooks_app.command(name="status")
def hooks_status_cmd() -> None:
    """Check hook installation status."""
    from .hooks.install import hooks_status
    s = hooks_status()
    if s["installed"]:
        console.print("[green]Hooks installed and configured[/green]")
    else:
        if not s["scripts_present"]:
            console.print("[red]Hook scripts missing[/red]")
        if not s["settings_configured"]:
            console.print("[red]Settings not configured[/red]")
        console.print("Run [bold]agentmesh hooks install[/bold] to fix")
