"""AgentMesh CLI -- local-first multi-agent coordination."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console

from . import __version__
from .models import Agent, AgentKind, AgentStatus, ClaimIntent, EventKind, Severity, TaskState, _now
from . import db, events, claims, messages, status, capsules, episodes, gitbridge, weaver

app = typer.Typer(name="agentmesh", help="Local-first multi-agent coordination substrate.")
console = Console()

_DATA_DIR: Path | None = None
EPISODE_TRAILER_KEY = "AgentMesh-Episode"


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


def _policy_path(cwd: Path | None = None) -> Path:
    base = cwd or Path.cwd()
    return base / ".agentmesh" / "policy.json"


def _load_policy(cwd: Path | None = None) -> dict[str, Any]:
    path = _policy_path(cwd)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _policy_get(policy: dict[str, Any], keys: list[str], default: Any) -> Any:
    value: Any = policy
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def _write_scaffold_file(path: Path, content: str, force: bool) -> str:
    """Write scaffold file and return status: created/updated/skipped."""
    existed = path.exists()
    if existed and not force:
        return "skipped"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return "updated" if existed else "created"


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
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
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        console.print("\n[dim]New here? Start with:[/dim] [bold]agentmesh init[/bold]")


@app.command(name="init")
def init_cmd(
    repo: str = typer.Option(".", "--repo", "-r", help="Target repository path"),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing scaffold files"),
    install_hooks: bool = typer.Option(False, "--install-hooks", help="Install Claude Code hooks"),
    write_policy: bool = typer.Option(True, "--policy/--no-policy", help="Write .agentmesh/policy.json"),
    test_command: str = typer.Option("pytest -q", "--test-command", help="Default test command for task finish"),
    claim_ttl: int = typer.Option(1800, "--claim-ttl", help="Default claim TTL in seconds"),
    capsule_default: bool = typer.Option(True, "--capsule-default/--no-capsule-default",
                                         help="Default capsule behavior for task finish"),
) -> None:
    """Initialize AgentMesh defaults in a repository."""
    target = Path(repo).resolve()
    if not target.exists() or not target.is_dir():
        console.print(f"Not a directory: {target}", style="red")
        raise typer.Exit(1)

    policy = {
        "schema_version": "1.0",
        "claims": {
            "ttl_seconds": claim_ttl,
        },
        "task_finish": {
            "run_tests": test_command,
            "capsule": capsule_default,
            "release_all": True,
            "end_episode": True,
        },
    }

    capabilities = {
        "schema_version": "1.0",
        "tool_name": "agentmesh",
        "tool_version": __version__,
        "recommended_defaults": {
            "commit_via_agentmesh": True,
            "capsule_on_finish": capsule_default,
            "end_episode_on_finish": True,
            "release_claims_on_finish": True,
            "claim_ttl_seconds": claim_ttl,
            "test_command": test_command,
        },
        "happy_path": {
            "start": "agentmesh task start --title <task_title> [--claim <resource> ...]",
            "finish": "agentmesh task finish --message <msg> [--run-tests <cmd>]",
        },
        "commands": {
            "init": "agentmesh init [--repo <path>] [--install-hooks] [--policy]",
            "task.start": "agentmesh task start --title <title> [--claim <resource> ...]",
            "task.finish": "agentmesh task finish --message <msg> [--run-tests <cmd>]",
            "resource.claim": "agentmesh claim <resource ...>",
            "resource.check": "agentmesh check <path>",
            "mesh.status": "agentmesh status",
            "git.commit": "agentmesh commit -m <msg> [--run-tests <cmd>] [--capsule]",
            "weave.verify": "agentmesh weave verify",
            "weave.export": "agentmesh weave export --md",
            "episode.export": "agentmesh episode export <episode_id>",
            "episode.import": "agentmesh episode import <meshpack_path>",
        },
        "resource_prefixes": [
            "PORT:<number>",
            "LOCK:<name>",
            "TEST_SUITE:<name>",
            "TEMP_DIR:<path>",
            "<file_path>",
        ],
        "agent_guidance": [
            "Prefer task.start/task.finish for basic workflows.",
            "Claim resources before editing shared files.",
            "Treat weave verify failures as blocking.",
        ],
    }

    agents_md = f"""# AgentMesh Repo Playbook

This repo uses AgentMesh as a local coordination + provenance layer around normal git workflows.

## Happy Path

```bash
agentmesh task start --title "<task>" --claim <resource>
# edit + stage as normal
git add <files...>
agentmesh task finish --message "<commit message>"
```

Default policy:
- claim TTL: `{claim_ttl}` seconds
- task finish test command: `{test_command}`
- task finish capsule default: `{str(capsule_default).lower()}`

## Useful Commands

- `agentmesh status`
- `agentmesh check <path>`
- `agentmesh weave verify`
- `agentmesh weave export --md`
"""

    files: list[tuple[Path, str]] = [
        (target / "AGENTS.md", agents_md),
        (
            target / ".agentmesh" / "capabilities.json",
            json.dumps(capabilities, indent=2) + "\n",
        ),
    ]
    if write_policy:
        files.append(
            (
                target / ".agentmesh" / "policy.json",
                json.dumps(policy, indent=2) + "\n",
            )
        )

    for path, content in files:
        status_label = _write_scaffold_file(path, content, force=force)
        console.print(f"{status_label:7} {path}")

    if install_hooks:
        from .hooks.install import install_hooks as install_hooks_fn
        actions = install_hooks_fn()
        for action in actions:
            console.print(f"hook: {action}")
        console.print("[green]Hooks installed[/green]")


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


# -- Doctor command --

@app.command(name="doctor")
def doctor_cmd() -> None:
    """Check environment and report what needs fixing."""
    import subprocess
    ok_count = 0
    warn_count = 0

    def ok(msg: str) -> None:
        nonlocal ok_count
        ok_count += 1
        console.print(f"  [green]OK[/green]  {msg}")

    def warn(msg: str, fix: str) -> None:
        nonlocal warn_count
        warn_count += 1
        console.print(f"  [yellow]WARN[/yellow]  {msg}")
        console.print(f"         [dim]Fix: {fix}[/dim]")

    def fail(msg: str, fix: str) -> None:
        nonlocal warn_count
        warn_count += 1
        console.print(f"  [red]FAIL[/red]  {msg}")
        console.print(f"         [dim]Fix: {fix}[/dim]")

    console.print("[bold]agentmesh doctor[/bold]\n")

    # 1. Git repo check
    try:
        subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True, check=True)
        ok("Inside a git repository")
    except (subprocess.CalledProcessError, FileNotFoundError):
        fail("Not inside a git repository", "cd into a git repo first")

    # 2. Initialized check
    am_dir = Path.cwd() / ".agentmesh"
    if am_dir.is_dir():
        ok(".agentmesh/ directory exists")
    else:
        fail("AgentMesh not initialized in this repo", "agentmesh init")

    # 3. Policy file check
    policy_path = am_dir / "policy.json"
    if policy_path.is_file():
        ok("policy.json present")
    else:
        warn("No policy.json found", "agentmesh init")

    # 4. Hooks check
    from .hooks.install import hooks_status as _hooks_status
    hs = _hooks_status()
    if hs.get("installed"):
        ok("Claude Code hooks installed")
    else:
        warn("Claude Code hooks not installed", "agentmesh hooks install")

    # 5. jq check (needed for agentmesh-action in CI)
    jq_found = shutil.which("jq") is not None
    if jq_found:
        ok("jq available (used by agentmesh-action)")
    else:
        warn("jq not found (optional, used by agentmesh-action in CI)", "brew install jq")

    # 6. Active episode check
    data_dir = _get_data_dir()
    try:
        db.init_db(data_dir)
        ep_id = episodes.get_current_episode(data_dir)
        if ep_id:
            ok(f"Active episode: {ep_id}")
        else:
            warn("No active episode", "agentmesh episode start --title 'my task'")
    except Exception:
        warn("Could not read database", "agentmesh init")

    console.print(f"\n  {ok_count} passed, {warn_count} issues")
    if warn_count == 0:
        console.print("  [green]Everything looks good.[/green]")


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
    diff_text = gitbridge.get_staged_diff(cwd)
    patch_hash = gitbridge.compute_patch_hash(diff_text)

    # Build trailer -- witness if key available, else episode-only
    trailer = ""
    witness_result = None
    if episode_trailer:
        try:
            from . import witness as _witness
            witness_result = _witness.create_and_sign(agent_id, cwd=cwd, data_dir=_get_data_dir())
        except ImportError as exc:
            missing = getattr(exc, "name", "") or ""
            if missing.startswith("cryptography") or missing == "agentmesh.witness":
                # Optional witness deps are not installed; keep commit flow working.
                witness_result = None
            else:
                raise

    if witness_result:
        _w, _w_hash, _sig, _kid, trailer = witness_result
    elif episode_trailer:
        ep_id = episodes.get_current_episode(_get_data_dir())
        if ep_id:
            trailer = f"{EPISODE_TRAILER_KEY}: {ep_id}"

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
            "witness_hash": witness_result[1] if witness_result else "",
        },
        data_dir=_get_data_dir(),
    )

    console.print(f"Committed [bold]{sha[:10]}[/bold]  weave={evt.event_id}")
    if witness_result:
        console.print(f"  witness={witness_result[1][:30]}... signed by {witness_result[3]}")
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
    ttl: Optional[int] = typer.Option(None, "--ttl", help="Claim TTL in seconds (default from policy)"),
    reuse_current: bool = typer.Option(
        True,
        "--reuse-current/--new-episode",
        help="Reuse current episode if one is active",
    ),
    orch_task: Optional[str] = typer.Option(None, "--orch-task", help="Orchestrator task ID to transition to RUNNING"),
) -> None:
    """Start a task: ensure an episode exists and optionally claim resources."""
    _ensure_db()
    agent_id = agent or _auto_agent_id()
    _ensure_agent_exists(agent_id)
    policy = _load_policy(Path.cwd())
    policy_ttl = _policy_get(policy, ["claims", "ttl_seconds"], 1800)
    effective_ttl = ttl if ttl is not None else (policy_ttl if isinstance(policy_ttl, int) else 1800)

    # Bridge to orchestrator if --orch-task is provided
    if orch_task:
        from . import orchestrator
        orch_t = db.get_task(orch_task, _get_data_dir())
        if orch_t is None:
            console.print(f"Orchestrator task {orch_task} not found", style="red")
            raise typer.Exit(1)
        try:
            orchestrator.transition_task(orch_task, TaskState.RUNNING, agent_id=agent_id, data_dir=_get_data_dir())
        except orchestrator.TransitionError as e:
            console.print(str(e), style="red")
            raise typer.Exit(1)
        console.print(f"Orch task [bold]{orch_task}[/bold] -> running")
        # Use the orchestrator task's episode if available
        if orch_t.episode_id:
            ep_id = orch_t.episode_id
            episodes.set_current_episode(ep_id, _get_data_dir())
            console.print(f"Using episode [bold]{ep_id}[/bold] (from orch task)")
            if not claim_resources:
                console.print("[dim]No claims requested[/dim]")
            else:
                had_conflict = False
                for resource in claim_resources:
                    ok, clm, conflicts = claims.make_claim(
                        agent_id, resource, intent=ClaimIntent.EDIT,
                        ttl_s=effective_ttl, reason=f"task:{title}",
                        data_dir=_get_data_dir(),
                    )
                    if ok:
                        rt_label = clm.resource_type.value.upper() if clm.resource_type.value != "file" else ""
                        prefix = f"[{rt_label}] " if rt_label else ""
                        console.print(f"Claimed {prefix}[bold]{clm.path}[/bold] (ttl={effective_ttl}s)")
                    else:
                        had_conflict = True
                        console.print(f"CONFLICT on [bold]{resource}[/bold]:", style="red bold")
                        console.print(claims.format_conflict(conflicts))
                if had_conflict:
                    raise typer.Exit(1)
            return

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
            ttl_s=effective_ttl,
            reason=f"task:{title}",
            data_dir=_get_data_dir(),
        )
        if ok:
            rt_label = clm.resource_type.value.upper() if clm.resource_type.value != "file" else ""
            prefix = f"[{rt_label}] " if rt_label else ""
            console.print(f"Claimed {prefix}[bold]{clm.path}[/bold] (ttl={effective_ttl}s)")
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
    capsule: Optional[bool] = typer.Option(None, "--capsule/--no-capsule",
                                           help="Emit a context capsule with the commit (default from policy)"),
    release_all: Optional[bool] = typer.Option(
        None,
        "--release-all/--keep-claims",
        help="Release all claims held by this agent after commit (default from policy)",
    ),
    end_episode: Optional[bool] = typer.Option(
        None,
        "--end-episode/--keep-episode",
        help="End the current episode after commit (default from policy)",
    ),
    orch_task: Optional[str] = typer.Option(None, "--orch-task", help="Orchestrator task ID to transition to PR_OPEN"),
) -> None:
    """Finish a task: commit with provenance, optionally release claims and end episode."""
    _ensure_db()
    agent_id = agent or _auto_agent_id()
    policy = _load_policy(Path.cwd())
    policy_finish = _policy_get(policy, ["task_finish"], {})

    effective_run_tests = run_tests
    if effective_run_tests is None and isinstance(policy_finish, dict):
        p_test = policy_finish.get("run_tests")
        if isinstance(p_test, str) and p_test.strip():
            effective_run_tests = p_test

    def _bool_default(value: Optional[bool], key: str, fallback: bool) -> bool:
        if value is not None:
            return value
        if isinstance(policy_finish, dict) and isinstance(policy_finish.get(key), bool):
            return policy_finish[key]
        return fallback

    effective_capsule = _bool_default(capsule, "capsule", True)
    effective_release_all = _bool_default(release_all, "release_all", True)
    effective_end_episode = _bool_default(end_episode, "end_episode", True)

    commit_cmd(
        message=message,
        agent=agent_id,
        episode_trailer=True,
        run_tests=effective_run_tests,
        capsule=effective_capsule,
    )

    # Bridge to orchestrator: transition to PR_OPEN after successful commit
    if orch_task:
        from . import orchestrator
        try:
            orchestrator.transition_task(orch_task, TaskState.PR_OPEN, agent_id=agent_id, data_dir=_get_data_dir())
            console.print(f"Orch task [bold]{orch_task}[/bold] -> pr_open")
        except orchestrator.TransitionError as e:
            console.print(f"Orch transition warning: {e}", style="yellow")

    if effective_release_all:
        released = claims.release(agent_id, release_all=True, data_dir=_get_data_dir())
        console.print(f"Released {released} claim(s)")

    if effective_end_episode:
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


# -- Orchestrator commands --

orch_app = typer.Typer(help="Task orchestrator lifecycle commands.")
app.add_typer(orch_app, name="orch")


@orch_app.command(name="create")
def orch_create(
    title: str = typer.Option(..., "--title", "-t", help="Task title"),
    description: str = typer.Option("", "--description", "-d", help="Task description"),
    episode: str = typer.Option("", "--episode", "-e", help="Episode ID to link"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Create a new orchestrator task (starts in PLANNED state)."""
    _ensure_db()
    from . import orchestrator
    task = orchestrator.create_task(title=title, description=description, episode_id=episode, data_dir=_get_data_dir())
    if json_out:
        console.print(json.dumps({"task_id": task.task_id, "state": task.state.value, "title": task.title}, indent=2))
    else:
        console.print(f"Created [bold]{task.task_id}[/bold]  state={task.state.value}")


@orch_app.command(name="assign")
def orch_assign(
    task_id: str = typer.Argument(..., help="Task ID"),
    agent: str = typer.Option("", "--agent", "-a", help="Agent ID (auto-detected if omitted)"),
    branch: str = typer.Option("", "--branch", "-b", help="Git branch"),
) -> None:
    """Assign a PLANNED task to an agent."""
    _ensure_db()
    from . import orchestrator
    agent_id = agent or _auto_agent_id()
    _ensure_agent_exists(agent_id)
    try:
        task = orchestrator.assign_task(task_id, agent_id, branch=branch, data_dir=_get_data_dir())
    except orchestrator.TransitionError as e:
        console.print(str(e), style="red")
        raise typer.Exit(1)
    console.print(f"Assigned [bold]{task.task_id}[/bold] to {agent_id}  state={task.state.value}")


@orch_app.command(name="advance")
def orch_advance(
    task_id: str = typer.Argument(..., help="Task ID"),
    to: str = typer.Option(..., "--to", help="Target state (running, pr_open, ci_pass, review_pass, merged)"),
    reason: str = typer.Option("", "--reason", "-r", help="Reason for transition"),
    pr_url: str = typer.Option("", "--pr-url", help="PR URL (for pr_open transition)"),
) -> None:
    """Advance a task to the next state."""
    _ensure_db()
    from . import orchestrator
    try:
        to_state = TaskState(to)
    except ValueError:
        valid = ", ".join(s.value for s in TaskState)
        console.print(f"Invalid state '{to}'. Valid: {valid}", style="red")
        raise typer.Exit(1)
    kwargs: dict[str, Any] = {}
    if pr_url:
        kwargs["pr_url"] = pr_url
    try:
        task = orchestrator.transition_task(task_id, to_state, reason=reason, data_dir=_get_data_dir(), **kwargs)
    except orchestrator.TransitionError as e:
        console.print(str(e), style="red")
        raise typer.Exit(1)
    console.print(f"Advanced [bold]{task.task_id}[/bold] to {task.state.value}")


@orch_app.command(name="abort")
def orch_abort(
    task_id: str = typer.Argument(..., help="Task ID"),
    reason: str = typer.Option("", "--reason", "-r", help="Abort reason"),
) -> None:
    """Abort a task from any non-terminal state."""
    _ensure_db()
    from . import orchestrator
    try:
        task = orchestrator.abort_task(task_id, reason=reason, data_dir=_get_data_dir())
    except orchestrator.TransitionError as e:
        console.print(str(e), style="red")
        raise typer.Exit(1)
    console.print(f"Aborted [bold]{task.task_id}[/bold]")


@orch_app.command(name="show")
def orch_show(
    task_id: str = typer.Argument(..., help="Task ID"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Show task details and attempts."""
    _ensure_db()
    task = db.get_task(task_id, _get_data_dir())
    if task is None:
        console.print(f"Task {task_id} not found", style="red")
        raise typer.Exit(1)
    attempts = db.list_attempts(task_id, _get_data_dir())
    if json_out:
        data = {
            "task_id": task.task_id,
            "title": task.title,
            "description": task.description,
            "state": task.state.value,
            "assigned_agent_id": task.assigned_agent_id,
            "branch": task.branch,
            "pr_url": task.pr_url,
            "episode_id": task.episode_id,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "attempts": [
                {"attempt_id": a.attempt_id, "agent_id": a.agent_id, "attempt_number": a.attempt_number,
                 "outcome": a.outcome, "started_at": a.started_at, "ended_at": a.ended_at}
                for a in attempts
            ],
        }
        console.print(json.dumps(data, indent=2))
    else:
        console.print(f"[bold]{task.task_id}[/bold]  {task.title}")
        console.print(f"  state={task.state.value}  agent={task.assigned_agent_id or '-'}  branch={task.branch or '-'}")
        if task.pr_url:
            console.print(f"  pr={task.pr_url}")
        if task.description:
            console.print(f"  desc={task.description}")
        console.print(f"  created={task.created_at[:19]}  updated={task.updated_at[:19]}")
        if attempts:
            console.print(f"  attempts ({len(attempts)}):")
            for a in attempts:
                outcome = a.outcome or "in_progress"
                console.print(f"    #{a.attempt_number} {a.agent_id} {outcome}")


@orch_app.command(name="list")
def orch_list(
    state: str = typer.Option("", "--state", "-s", help="Filter by state"),
    agent: str = typer.Option("", "--agent", "-a", help="Filter by assigned agent"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """List orchestrator tasks."""
    _ensure_db()
    filter_state = TaskState(state) if state else None
    tasks = db.list_tasks(data_dir=_get_data_dir(), state=filter_state, assigned_agent_id=agent or None)
    if json_out:
        data = [
            {"task_id": t.task_id, "title": t.title, "state": t.state.value,
             "assigned_agent_id": t.assigned_agent_id, "branch": t.branch}
            for t in tasks
        ]
        console.print(json.dumps(data, indent=2))
    else:
        if not tasks:
            console.print("[dim]No tasks[/dim]")
            return
        for t in tasks:
            agent_str = t.assigned_agent_id or "-"
            console.print(f"  {t.task_id}  {t.state.value:12}  {agent_str:20}  {t.title}")


# -- Watchdog command --

@app.command(name="watchdog")
def watchdog_cmd(
    threshold: int = typer.Option(300, "--threshold", "-t", help="Stale threshold in seconds"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Run watchdog scan: detect stale agents, reap them, abort their tasks."""
    _ensure_db()
    from . import watchdog
    result = watchdog.scan(stale_threshold_s=threshold, data_dir=_get_data_dir())
    if json_out:
        data = {
            "stale_agents": result.stale_agents,
            "reaped_agents": result.reaped_agents,
            "aborted_tasks": result.aborted_tasks,
            "clean": result.clean,
        }
        console.print(json.dumps(data, indent=2))
    elif result.clean:
        console.print("[green]Clean[/green] -- no stale agents")
    else:
        console.print(f"Stale agents: {len(result.stale_agents)}")
        for a in result.stale_agents:
            console.print(f"  reaped: {a}")
        if result.aborted_tasks:
            console.print(f"Aborted tasks: {len(result.aborted_tasks)}")
            for t in result.aborted_tasks:
                console.print(f"  {t}")


# -- Witness commands --

witness_app = typer.Typer(help="Witness envelope commands.")
app.add_typer(witness_app, name="witness")


@witness_app.command(name="verify")
def witness_verify_cmd(
    commit: str = typer.Argument("HEAD", help="Commit SHA to verify"),
) -> None:
    """Verify a commit's witness envelope."""
    try:
        from . import witness as _witness
    except ImportError as exc:
        missing = getattr(exc, "name", "") or ""
        if not (missing.startswith("cryptography") or missing == "agentmesh.witness"):
            raise
        console.print(
            "Witness support not installed. Run: pip install 'agentmesh-core[witness]'",
            style="red",
            markup=False,
        )
        raise typer.Exit(1)
    result = _witness.verify_commit(commit, cwd=os.getcwd(), data_dir=_get_data_dir())
    if result.ok:
        console.print(f"[green]VERIFIED[/green]  {result.details}")
    elif result.status == "NO_TRAILERS":
        console.print(f"[dim]NO_TRAILERS[/dim]  {result.details}")
    elif result.status == "WITNESS_MISSING":
        console.print(f"[yellow]WITNESS_MISSING[/yellow]  {result.details}")
    else:
        console.print(f"[red]{result.status}[/red]  {result.details}")
        raise typer.Exit(1)


# -- Key commands --

key_app = typer.Typer(help="Signing key management.")
app.add_typer(key_app, name="key")


@key_app.command(name="generate")
def key_generate_cmd() -> None:
    """Generate a new Ed25519 signing key."""
    try:
        from . import keystore as _ks
    except ImportError as exc:
        missing = getattr(exc, "name", "") or ""
        if not (missing.startswith("cryptography") or missing == "agentmesh.keystore"):
            raise
        console.print(
            "Witness support not installed. Run: pip install 'agentmesh-core[witness]'",
            style="red",
            markup=False,
        )
        raise typer.Exit(1)
    kid, _priv = _ks.generate_key(_get_data_dir())
    console.print(f"Generated key [bold]{kid}[/bold]")


@key_app.command(name="list")
def key_list_cmd() -> None:
    """List signing keys."""
    try:
        from . import keystore as _ks
    except ImportError as exc:
        missing = getattr(exc, "name", "") or ""
        if not (missing.startswith("cryptography") or missing == "agentmesh.keystore"):
            raise
        console.print(
            "Witness support not installed. Run: pip install 'agentmesh-core[witness]'",
            style="red",
            markup=False,
        )
        raise typer.Exit(1)
    kids = _ks.list_keys(_get_data_dir())
    if not kids:
        console.print("[dim]No keys. Run: agentmesh key generate[/dim]")
        return
    for kid in kids:
        console.print(f"  {kid}")


# -- MCP commands --

mcp_app = typer.Typer(help="MCP server management.")
app.add_typer(mcp_app, name="mcp")


@mcp_app.command(name="serve")
def mcp_serve() -> None:
    """Start the AgentMesh MCP server (stdio transport)."""
    try:
        from .mcp_server import main as mcp_main
    except ImportError:
        # Rich markup treats [mcp] as a tag unless markup is disabled.
        console.print(
            "MCP support not installed. Run: pip install 'agentmesh-core[mcp]'",
            style="red",
            markup=False,
        )
        raise typer.Exit(1)
    mcp_main()


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
