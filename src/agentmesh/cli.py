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
from . import db, events, claims, messages, status, capsules, episodes, gitbridge, weaver

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


def _ensure_agent_exists(agent_id: str) -> None:
    """Ensure the agent exists for claim operations (claims have FK to agents)."""
    if db.get_agent(agent_id, _get_data_dir()) is not None:
        return
    now = _now()
    a = Agent(
        agent_id=agent_id,
        kind=AgentKind.CLAUDE_CODE,
        display_name=agent_id,
        cwd=os.getcwd(),
        pid=os.getpid(),
        tty=os.environ.get("TTY", ""),
        status=AgentStatus.IDLE,
        registered_at=now,
        last_heartbeat=now,
    )
    db.register_agent(a, _get_data_dir())
    events.append_event(
        EventKind.REGISTER,
        agent_id=agent_id,
        payload={"kind": AgentKind.CLAUDE_CODE.value, "name": agent_id, "cwd": os.getcwd()},
        data_dir=_get_data_dir(),
    )


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
    _ensure_agent_exists(agent_id)
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


@episode_app.command(name="export")
def episode_export_cmd(
    episode_id: str = typer.Argument(..., help="Episode ID to export"),
    out: Optional[str] = typer.Option(None, "--out", "-o", help="Output path"),
) -> None:
    """Export an episode as a .meshpack bundle."""
    _ensure_db()
    from .passport import export_episode
    out_path = Path(out) if out else None
    result = export_episode(episode_id, output_path=out_path, data_dir=_get_data_dir())
    console.print(f"Exported to [bold]{result}[/bold]")


@episode_app.command(name="verify")
def episode_verify_cmd(
    pack_path: str = typer.Argument(..., help="Path to .meshpack file"),
) -> None:
    """Verify a .meshpack bundle signature."""
    _ensure_db()
    from .passport import verify_meshpack
    valid, manifest = verify_meshpack(Path(pack_path))
    if valid:
        console.print("[green]Signature valid[/green]")
        console.print(f"  episode={manifest['episode_id']}  counts={manifest['counts']}")
    else:
        console.print("[red]Signature INVALID[/red]")
        raise typer.Exit(1)


@episode_app.command(name="import")
def episode_import_cmd(
    pack_path: str = typer.Argument(..., help="Path to .meshpack file"),
    namespace: str = typer.Option("", "--namespace", "-n", help="Namespace prefix for imported episode"),
) -> None:
    """Import a .meshpack bundle into the local DB."""
    _ensure_db()
    from .passport import import_meshpack
    counts = import_meshpack(Path(pack_path), namespace=namespace, data_dir=_get_data_dir())
    console.print(f"Imported: {counts}")


# -- Wait/Steal commands --

@app.command()
def wait(
    resource: str = typer.Argument(..., help="Resource to wait on (paths, PORT:N, etc.)"),
    agent: Optional[str] = typer.Option(None, "--agent", "-a"),
    priority: int = typer.Option(5, "--priority", "-p", help="Wait priority (1-10)"),
    reason: str = typer.Option("", "--reason", "-r"),
) -> None:
    """Register a wait on a claimed resource. Triggers priority inheritance on the holder."""
    _ensure_db()
    from .waiters import register_wait
    from .claims import parse_resource_string
    agent_id = agent or _auto_agent_id()
    rt, norm = parse_resource_string(resource)
    w = register_wait(
        agent_id, norm, priority=priority, reason=reason,
        resource_type=rt, data_dir=_get_data_dir(),
    )
    events.append_event(
        EventKind.WAIT, agent_id=agent_id,
        payload={"resource": norm, "resource_type": rt.value, "priority": priority, "reason": reason},
        data_dir=_get_data_dir(),
    )
    console.print(f"Wait [bold]{w.waiter_id}[/bold] registered on {norm} (priority={priority})")


@app.command()
def steal(
    resource: str = typer.Argument(..., help="Resource to steal"),
    agent: Optional[str] = typer.Option(None, "--agent", "-a"),
    reason: str = typer.Option("", "--reason", "-r"),
    priority: int = typer.Option(5, "--priority", "-p"),
    stale_threshold: int = typer.Option(300, "--stale-threshold", help="Stale threshold in seconds"),
) -> None:
    """Attempt to steal a resource claim (only if TTL expired or holder heartbeat stale)."""
    _ensure_db()
    from .waiters import steal_resource
    from .claims import parse_resource_string
    agent_id = agent or _auto_agent_id()
    rt, norm = parse_resource_string(resource)
    ok, msg_text = steal_resource(
        agent_id, norm, reason=reason, priority=priority,
        resource_type=rt, stale_threshold_s=stale_threshold,
        data_dir=_get_data_dir(),
    )
    if ok:
        events.append_event(
            EventKind.STEAL, agent_id=agent_id,
            payload={"resource": norm, "resource_type": rt.value, "reason": msg_text},
            data_dir=_get_data_dir(),
        )
        console.print(f"Stole [bold]{norm}[/bold] ({msg_text})")
    else:
        console.print(f"Steal failed: {msg_text}", style="red")
        raise typer.Exit(1)


# -- Commit command (git-weave bridge) --

@app.command(name="commit")
def commit_cmd(
    message: str = typer.Option(..., "--message", "-m", help="Commit message"),
    agent: Optional[str] = typer.Option(None, "--agent", "-a"),
    episode_trailer: bool = typer.Option(True, "--episode-trailer/--no-episode-trailer",
                                         help="Append episode ID trailer to commit message"),
    run_tests: Optional[str] = typer.Option(None, "--run-tests", help="Test command to run before commit"),
    capsule: bool = typer.Option(False, "--capsule", help="Also emit a context capsule"),
) -> None:
    """Wrap git commit with provenance: auto-creates a weave event linking the commit to the episode."""
    _ensure_db()
    agent_id = agent or _auto_agent_id()
    cwd = os.getcwd()

    if not gitbridge.is_git_repo(cwd):
        console.print("Not a git repository", style="red")
        raise typer.Exit(1)

    staged_files = gitbridge.get_staged_files(cwd)
    if not staged_files:
        console.print("Nothing staged to commit", style="red")
        raise typer.Exit(1)

    if run_tests:
        console.print(f"Running tests: {run_tests}")
        passed, summary = gitbridge.run_tests(run_tests, cwd=cwd)
        if not passed:
            console.print(f"Tests failed, aborting commit:\n{summary}", style="red")
            raise typer.Exit(1)
        console.print("[green]Tests passed[/green]")
        # Recompute after tests (tests may have re-staged files)
        staged_files = gitbridge.get_staged_files(cwd)

    # Compute patch hash from final staged state (after any test mutations)
    patch_hash = gitbridge.compute_patch_hash(gitbridge.get_staged_diff(cwd))

    # Build trailer
    trailer = ""
    if episode_trailer:
        ep_id = episodes.get_current_episode(_get_data_dir())
        if ep_id:
            trailer = f"AgentMesh-Episode: {ep_id}"

    ok, sha, err = gitbridge.git_commit(message, trailer=trailer, cwd=cwd)
    if not ok:
        console.print(f"git commit failed: {err}", style="red")
        raise typer.Exit(1)

    # Capsule if requested (before weave, so we can link capsule_id)
    capsule_id = ""
    if capsule:
        cap = capsules.build_capsule(agent_id, task_desc=message, cwd=cwd, data_dir=_get_data_dir())
        capsule_id = cap.capsule_id

    # Weave event (linked to capsule if created)
    evt = weaver.append_weave(
        capsule_id=capsule_id,
        git_commit_sha=sha,
        git_patch_hash=patch_hash,
        affected_symbols=staged_files,
        data_dir=_get_data_dir(),
    )

    # Event log
    events.append_event(
        EventKind.COMMIT, agent_id=agent_id,
        payload={
            "sha": sha,
            "patch_hash": patch_hash,
            "files": staged_files,
            "weave_event_id": evt.event_id,
        },
        data_dir=_get_data_dir(),
    )

    console.print(f"Committed [bold]{sha[:10]}[/bold]  weave={evt.event_id}")
    console.print(f"  {len(staged_files)} file(s): {', '.join(staged_files[:5])}")


# -- Task commands (happy-path wrappers) --

task_app = typer.Typer(help="Happy-path task workflow commands.")
app.add_typer(task_app, name="task")


@task_app.command(name="start")
def task_start(
    title: str = typer.Option(..., "--title", "-t", help="Task title for the episode"),
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="Agent ID"),
    claim_resources: list[str] = typer.Option(
        [],
        "--claim",
        "-c",
        help="Resource to claim (repeat for multiple resources)",
    ),
    ttl: int = typer.Option(1800, "--ttl", help="Claim TTL in seconds"),
    reuse_current: bool = typer.Option(
        True,
        "--reuse-current/--new-episode",
        help="Reuse current episode if one is active",
    ),
) -> None:
    """Start a task: ensure an episode exists and optionally claim resources."""
    _ensure_db()
    agent_id = agent or _auto_agent_id()
    _ensure_agent_exists(agent_id)

    ep_id = episodes.get_current_episode(_get_data_dir()) if reuse_current else ""
    created_new = False
    if not ep_id:
        ep_id = episodes.start_episode(title=title, data_dir=_get_data_dir())
        events.append_event(
            EventKind.EPISODE_START,
            payload={"episode_id": ep_id, "title": title},
            data_dir=_get_data_dir(),
        )
        created_new = True

    if created_new:
        console.print(f"Episode [bold]{ep_id}[/bold] started")
    else:
        console.print(f"Using episode [bold]{ep_id}[/bold]")

    if not claim_resources:
        console.print("[dim]No claims requested[/dim]")
        return

    had_conflict = False
    for resource in claim_resources:
        ok, clm, conflicts = claims.make_claim(
            agent_id,
            resource,
            intent=ClaimIntent.EDIT,
            ttl_s=ttl,
            reason=f"task:{title}",
            data_dir=_get_data_dir(),
        )
        if ok:
            rt_label = clm.resource_type.value.upper() if clm.resource_type.value != "file" else ""
            prefix = f"[{rt_label}] " if rt_label else ""
            console.print(f"Claimed {prefix}[bold]{clm.path}[/bold] (ttl={ttl}s)")
        else:
            had_conflict = True
            console.print(f"CONFLICT on [bold]{resource}[/bold]:", style="red bold")
            console.print(claims.format_conflict(conflicts))
    if had_conflict:
        raise typer.Exit(1)


@task_app.command(name="finish")
def task_finish(
    message: str = typer.Option(..., "--message", "-m", help="Commit message"),
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="Agent ID"),
    run_tests: Optional[str] = typer.Option(None, "--run-tests", help="Test command to run before commit"),
    capsule: bool = typer.Option(True, "--capsule/--no-capsule", help="Emit a context capsule with the commit"),
    release_all: bool = typer.Option(
        True,
        "--release-all/--keep-claims",
        help="Release all claims held by this agent after commit",
    ),
    end_episode: bool = typer.Option(
        True,
        "--end-episode/--keep-episode",
        help="End the current episode after commit",
    ),
) -> None:
    """Finish a task: commit with provenance, optionally release claims and end episode."""
    _ensure_db()
    agent_id = agent or _auto_agent_id()

    commit_cmd(
        message=message,
        agent=agent_id,
        episode_trailer=True,
        run_tests=run_tests,
        capsule=capsule,
    )

    if release_all:
        released = claims.release(agent_id, release_all=True, data_dir=_get_data_dir())
        console.print(f"Released {released} claim(s)")

    if end_episode:
        ep_id = episodes.end_episode(_get_data_dir())
        if ep_id:
            events.append_event(
                EventKind.EPISODE_END,
                payload={"episode_id": ep_id},
                data_dir=_get_data_dir(),
            )
            console.print(f"Episode [bold]{ep_id}[/bold] ended")
        else:
            console.print("[dim]No active episode to end[/dim]")


# -- Weave commands --

weave_app = typer.Typer(help="Provenance weave commands.")
app.add_typer(weave_app, name="weave")


@weave_app.command(name="record")
def weave_record(
    capsule_id: str = typer.Option("", "--capsule-id", "-c"),
    commit: str = typer.Option("", "--commit", help="Git commit SHA"),
    patch_hash: str = typer.Option("", "--patch-hash"),
    symbols: Optional[str] = typer.Option(None, "--symbols", "-s", help="Comma-separated affected symbols"),
    trace_id: str = typer.Option("", "--trace-id"),
    parent: str = typer.Option("", "--parent", "-p", help="Parent event ID"),
) -> None:
    """Record a provenance weave event."""
    _ensure_db()
    from . import weaver
    syms = [s.strip() for s in symbols.split(",")] if symbols else []
    evt = weaver.append_weave(
        capsule_id=capsule_id, git_commit_sha=commit,
        git_patch_hash=patch_hash, affected_symbols=syms,
        trace_id=trace_id, parent_event_id=parent,
        data_dir=_get_data_dir(),
    )
    console.print(f"Weave [bold]{evt.event_id}[/bold] recorded")


@weave_app.command(name="verify")
def weave_verify() -> None:
    """Verify the weave hash chain."""
    _ensure_db()
    from . import weaver
    valid, err = weaver.verify_weave(_get_data_dir())
    if valid:
        console.print("[green]Weave chain valid[/green]")
    else:
        console.print(f"[red]Weave chain BROKEN[/red]: {err}")
        raise typer.Exit(1)


@weave_app.command(name="trace")
def weave_trace(
    path: str = typer.Argument(..., help="File path to trace"),
) -> None:
    """Trace provenance for a file."""
    _ensure_db()
    from . import weaver
    evts = weaver.trace_file(path, data_dir=_get_data_dir())
    if not evts:
        console.print(f"[dim]No weave events for {path}[/dim]")
        return
    for e in evts:
        console.print(f"  {e.event_id}  commit={e.git_commit_sha or '-'}  capsule={e.capsule_id or '-'}")


@weave_app.command(name="export")
def weave_export(
    md: bool = typer.Option(False, "--md", help="Export as Markdown"),
    episode: Optional[str] = typer.Option(None, "--episode", "-e"),
) -> None:
    """Export weave events."""
    _ensure_db()
    from . import weaver
    if md:
        output = weaver.export_weave_md(episode_id=episode, data_dir=_get_data_dir())
        console.print(output)
    else:
        evts = db.list_weave_events(_get_data_dir(), episode_id=episode)
        console.print(json.dumps([e.model_dump() for e in evts], indent=2))


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
