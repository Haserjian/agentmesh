"""WorkerSpawner -- bridge orchestrator tasks to Claude Code processes in git worktrees."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import db, events, orch_control, orchestrator, weaver
from .gitbridge import create_worktree, remove_worktree, run_tests
from .models import EventKind, TaskState, _now
from .worker_adapters import (
    WorkerOutput,
    describe_adapter,
    enforce_adapter_policy,
    get_adapter,
    normalize_worker_output,
)


class SpawnError(Exception):
    """Raised when a spawn operation fails."""


@dataclass
class SpawnRecord:
    spawn_id: str
    task_id: str
    attempt_id: str
    agent_id: str
    pid: int
    worktree_path: str
    branch: str
    episode_id: str
    context_hash: str
    started_at: str
    ended_at: str = ""
    outcome: str = ""  # "" | "success" | "failure" | "aborted"
    output_path: str = ""
    repo_cwd: str = ""
    timeout_s: int = 0  # 0 = no timeout
    pid_started_at: float = 0.0  # epoch when PID was created (PID-reuse guard)
    backend: str = "claude_code"
    backend_version: str = ""


@dataclass
class CheckResult:
    spawn_id: str
    running: bool
    exit_code: int | None = None


@dataclass
class HarvestResult:
    spawn_id: str
    outcome: str
    output_data: dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    verification_command: str = ""
    verification_passed: bool | None = None
    verification_summary: str = ""


# ---------------------------------------------------------------------------
# Persistence helpers (SQLite-backed via db module)
# ---------------------------------------------------------------------------

def _row_to_record(row: dict[str, Any]) -> SpawnRecord:
    return SpawnRecord(
        spawn_id=row["spawn_id"],
        task_id=row["task_id"],
        attempt_id=row["attempt_id"],
        agent_id=row["agent_id"],
        pid=row["pid"],
        worktree_path=row["worktree_path"],
        branch=row["branch"],
        episode_id=row["episode_id"],
        context_hash=row["context_hash"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        outcome=row["outcome"],
        output_path=row["output_path"],
        repo_cwd=row["repo_cwd"],
        timeout_s=row.get("timeout_s", 0) if isinstance(row, dict) else row["timeout_s"],
        pid_started_at=row.get("pid_started_at", 0.0) if isinstance(row, dict) else row["pid_started_at"],
        backend=row.get("backend", "claude_code") if isinstance(row, dict) else row["backend"],
        backend_version=(
            row.get("backend_version", "") if isinstance(row, dict)
            else row["backend_version"]
        ),
    )


def _get_spawn(spawn_id: str, data_dir: Path | None = None) -> SpawnRecord:
    row = db.get_spawn(spawn_id, data_dir)
    if row is None:
        raise SpawnError(f"Spawn {spawn_id} not found")
    return _row_to_record(row)


def _resolve_repo_cwd(record: SpawnRecord) -> str | None:
    """Best-effort repository root for worktree cleanup."""
    if record.repo_cwd:
        return record.repo_cwd
    wt = Path(record.worktree_path).resolve()
    for parent in wt.parents:
        if parent.name == ".worktrees":
            return str(parent.parent)
    return None


def _load_repo_policy(repo_cwd: str) -> dict[str, Any]:
    if not repo_cwd:
        return {}
    path = Path(repo_cwd) / ".agentmesh" / "policy.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _verification_command(task_meta: dict[str, Any], repo_cwd: str) -> str:
    """Resolve independent verification test command.

    Priority:
      1) task.meta.verify_tests_command
      2) policy orchestrator.test_verification.command when enabled=true
      3) disabled (empty)
    """
    cmd = task_meta.get("verify_tests_command", "") if isinstance(task_meta, dict) else ""
    if isinstance(cmd, str) and cmd.strip():
        return cmd.strip()

    policy = _load_repo_policy(repo_cwd)
    orch = policy.get("orchestrator", {}) if isinstance(policy, dict) else {}
    tv = orch.get("test_verification", {}) if isinstance(orch, dict) else {}
    if not isinstance(tv, dict):
        return ""

    enabled = tv.get("enabled", False)
    command = tv.get("command", "")
    if isinstance(enabled, bool) and enabled and isinstance(command, str) and command.strip():
        return command.strip()
    return ""


def _trim_summary(text: str, max_chars: int = 1000) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _cleanup_worktree(record: SpawnRecord) -> None:
    repo_cwd = _resolve_repo_cwd(record)
    remove_worktree(record.worktree_path, cwd=repo_cwd, force=True)


def _get_pid_create_time(pid: int) -> float:
    """Best-effort process creation time (epoch float). Returns 0.0 on failure.

    Uses ``ps -o lstart= -p <pid>`` which works on macOS and Linux without
    extra dependencies. Falls back to /proc on Linux if ps is unavailable.
    """
    import platform
    from datetime import datetime as _dt, timezone as _tz

    # Universal approach: ps -o lstart (works macOS + Linux)
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            # Format: "Mon Jan  1 12:00:00 2024"
            raw = " ".join(result.stdout.strip().split())  # normalize whitespace
            dt = _dt.strptime(raw, "%a %b %d %H:%M:%S %Y")
            # ps lstart is local time; convert to epoch
            return dt.replace(tzinfo=None).timestamp()
    except Exception:
        pass

    # Linux fallback: /proc/<pid>/stat
    if platform.system() == "Linux":
        try:
            with open(f"/proc/{pid}/stat", "r") as f:
                stat = f.read()
            fields = stat.rsplit(")", 1)[-1].split()
            starttime_ticks = int(fields[19])
            clk_tck = os.sysconf("SC_CLK_TCK")
            with open("/proc/stat", "r") as f:
                for line in f:
                    if line.startswith("btime "):
                        boot_time = int(line.split()[1])
                        return boot_time + starttime_ticks / clk_tck
        except Exception:
            pass

    return 0.0


def _terminate_pid(pid: int) -> None:
    """Best-effort termination for detached worker processes."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def spawn(
    task_id: str,
    agent_id: str,
    repo_cwd: str,
    model: str = "sonnet",
    timeout_s: int = 0,
    backend: str = "claude_code",
    data_dir: Path | None = None,
) -> SpawnRecord:
    """Spawn a Claude Code worker for an ASSIGNED orchestrator task.

    1. Validate task is ASSIGNED with a branch set
    2. Create worktree
    3. Build context prompt, hash it
    4. Start claude subprocess
    5. Transition task ASSIGNED -> RUNNING
    6. Persist SpawnRecord, emit receipt + event
    """
    if orch_control.is_frozen(data_dir):
        raise SpawnError("Orchestrator is frozen; new spawns are blocked")

    task = db.get_task(task_id, data_dir)
    if task is None:
        raise SpawnError(f"Task {task_id} not found")
    if task.state != TaskState.ASSIGNED:
        raise SpawnError(f"Task {task_id} not in ASSIGNED state (is {task.state.value})")
    if not task.branch:
        raise SpawnError(f"Task {task_id} has no branch set")

    # Resolve adapter early so unknown backend fails fast.
    try:
        adapter = get_adapter(backend)
    except ValueError as exc:
        raise SpawnError(str(exc)) from exc
    try:
        enforce_adapter_policy(backend, repo_cwd=repo_cwd)
    except ValueError as exc:
        raise SpawnError(str(exc)) from exc

    meta = describe_adapter(backend)
    backend_version = getattr(adapter, "version", "") or ""

    spawn_id = f"spawn_{uuid.uuid4().hex[:12]}"
    repo_root = str(Path(repo_cwd).resolve())

    # Worktree path
    wt_dir = Path(repo_root) / ".worktrees" / spawn_id
    wt_dir.parent.mkdir(parents=True, exist_ok=True)

    ok, err = create_worktree(task.branch, str(wt_dir), cwd=repo_root)
    if not ok:
        raise SpawnError(f"Failed to create worktree: {err}")

    # Build context prompt
    context = f"Task: {task.title}\n"
    if task.description:
        context += f"Description: {task.description}\n"
    context += f"Branch: {task.branch}\n"
    context_hash = f"sha256:{hashlib.sha256(context.encode()).hexdigest()}"

    # Build spawn spec via adapter
    am_dir = wt_dir / ".agentmesh"
    am_dir.mkdir(parents=True, exist_ok=True)
    spec = adapter.build_spawn_spec(
        context=context, model=model,
        worktree_path=wt_dir, output_dir=am_dir,
    )
    output_path = Path(spec.output_path)
    cmd = spec.command

    # Helper record for cleanup on failure (before DB insert)
    def _tmp_record(pid: int = 0) -> SpawnRecord:
        return SpawnRecord(
            spawn_id=spawn_id, task_id=task_id, attempt_id="",
            agent_id=agent_id, pid=pid, worktree_path=str(wt_dir),
            branch=task.branch, episode_id=task.episode_id,
            context_hash=context_hash, started_at=_now(),
            output_path=str(output_path), repo_cwd=repo_root,
            backend=backend,
            backend_version=backend_version,
        )

    try:
        popen_env = {**os.environ, **spec.env} if spec.env else None
        if spec.stdout_to_file:
            out_f = open(output_path, "w")
            proc = subprocess.Popen(
                cmd,
                cwd=str(wt_dir),
                stdout=out_f,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=popen_env,
            )
            out_f.close()
        else:
            proc = subprocess.Popen(
                cmd,
                cwd=str(wt_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=popen_env,
            )
    except OSError as exc:
        _cleanup_worktree(_tmp_record())
        raise SpawnError(f"Failed to start worker process: {exc}") from exc

    # Transition to RUNNING
    try:
        orchestrator.transition_task(
            task_id, TaskState.RUNNING,
            agent_id=agent_id,
            reason=f"spawned {spawn_id}",
            data_dir=data_dir,
        )
    except Exception as exc:
        _terminate_pid(proc.pid)
        _cleanup_worktree(_tmp_record(proc.pid))
        raise SpawnError(f"Failed to transition task to RUNNING: {exc}") from exc

    # Get attempt
    attempts = db.list_attempts(task_id, data_dir)
    attempt_id = attempts[-1].attempt_id if attempts else ""

    # Capture PID creation time for reuse detection
    pid_create_ts = _get_pid_create_time(proc.pid)

    now = _now()
    record = SpawnRecord(
        spawn_id=spawn_id,
        task_id=task_id,
        attempt_id=attempt_id,
        agent_id=agent_id,
        pid=proc.pid,
        worktree_path=str(wt_dir),
        branch=task.branch,
        episode_id=task.episode_id,
        context_hash=context_hash,
        started_at=now,
        output_path=str(output_path),
        repo_cwd=repo_root,
        timeout_s=timeout_s,
        pid_started_at=pid_create_ts,
        backend=backend,
        backend_version=backend_version,
    )

    db.create_spawn(
        spawn_id=spawn_id, task_id=task_id, attempt_id=attempt_id,
        agent_id=agent_id, pid=proc.pid, worktree_path=str(wt_dir),
        branch=task.branch, episode_id=task.episode_id,
        context_hash=context_hash, started_at=now,
        output_path=str(output_path), repo_cwd=repo_root,
        timeout_s=timeout_s, pid_started_at=pid_create_ts,
        backend=backend,
        backend_version=backend_version,
        data_dir=data_dir,
    )

    # Emit receipt
    weaver.append_weave(
        trace_id=spawn_id,
        episode_id=task.episode_id or None,
        data_dir=data_dir,
    )

    events.append_event(
        kind=EventKind.ADAPTER_LOAD,
        agent_id=agent_id,
        payload={
            "spawn_id": spawn_id,
            "backend": backend,
            "backend_version": backend_version,
            "module": meta.module,
            "origin": meta.origin,
        },
        data_dir=data_dir,
    )

    events.append_event(
        kind=EventKind.WORKER_SPAWN,
        agent_id=agent_id,
        payload={
            "spawn_id": spawn_id,
            "task_id": task_id,
            "attempt_id": attempt_id,
            "pid": proc.pid,
            "branch": task.branch,
            "context_hash": context_hash,
            "backend": backend,
            "backend_version": backend_version,
        },
        data_dir=data_dir,
    )

    return record


def check(spawn_id: str, data_dir: Path | None = None) -> CheckResult:
    """Poll-only liveness check. No side effects, no receipts."""
    record = _get_spawn(spawn_id, data_dir)

    if record.ended_at:
        # Already harvested/aborted
        exit_code = 0 if record.outcome == "success" else 1
        return CheckResult(spawn_id=spawn_id, running=False, exit_code=exit_code)

    try:
        os.kill(record.pid, 0)
        return CheckResult(spawn_id=spawn_id, running=True, exit_code=None)
    except ProcessLookupError:
        return CheckResult(spawn_id=spawn_id, running=False, exit_code=None)
    except PermissionError:
        # Process exists but we can't signal it
        return CheckResult(spawn_id=spawn_id, running=True, exit_code=None)


def harvest(
    spawn_id: str,
    cleanup_worktree: bool = True,
    data_dir: Path | None = None,
) -> HarvestResult:
    """Collect output from a finished worker.

    1. Verify process exited
    2. Read output via adapter
    3. Claim finalization atomically (CAS on ended_at)
    4. Side effects: transition task, end attempt, receipt, event
    5. Clean up worktree
    """
    record = _get_spawn(spawn_id, data_dir)

    if record.ended_at:
        raise SpawnError(f"Spawn {spawn_id} already harvested")

    status = check(spawn_id, data_dir)
    if status.running:
        raise SpawnError(f"Spawn {spawn_id} still running (pid={record.pid})")

    # Read output via adapter. If backend is unknown (e.g., plugin not loaded
    # in this runtime), fail closed instead of crashing watchdog/CLI.
    try:
        adapter = get_adapter(record.backend)
    except ValueError:
        worker_out = WorkerOutput(
            success=False,
            raw={"error": "unknown_backend", "backend": record.backend},
            error_message=f"unknown backend: {record.backend}",
        )
    else:
        worker_out = normalize_worker_output(
            adapter.parse_output(Path(record.output_path))
        )

    success = worker_out.success
    output_data = worker_out.raw
    outcome = "success" if success else "failure"
    verify_cmd = ""
    verify_passed: bool | None = None
    verify_summary = ""

    # Claim finalization atomically BEFORE any side effects.
    # If another caller (watchdog, manual CLI) finalized first, bail out.
    now = _now()
    claimed = db.finalize_spawn(spawn_id, ended_at=now, outcome=outcome, data_dir=data_dir)
    if not claimed:
        raise SpawnError(f"Spawn {spawn_id} already finalized (race)")

    # -- Side effects: only the winner of the CAS reaches here --
    task_for_meta = db.get_task(record.task_id, data_dir)
    task_meta = task_for_meta.meta if task_for_meta is not None else {}
    verify_cmd = _verification_command(task_meta, record.repo_cwd)
    if success and verify_cmd:
        verify_passed, verify_summary = run_tests(verify_cmd, cwd=record.worktree_path)
        if not verify_passed:
            success = False
            outcome = "failure"
            output_data = {
                **output_data,
                "error": "test_mismatch",
                "verify_tests_command": verify_cmd,
                "verify_summary": _trim_summary(verify_summary),
            }
            db.update_spawn(spawn_id, outcome=outcome, data_dir=data_dir)
            events.append_event(
                kind=EventKind.TEST_MISMATCH,
                agent_id=record.agent_id,
                payload={
                    "spawn_id": spawn_id,
                    "task_id": record.task_id,
                    "command": verify_cmd,
                    "summary": _trim_summary(verify_summary),
                },
                data_dir=data_dir,
            )

    transition_error = ""

    # Transition task
    if success:
        try:
            orchestrator.transition_task(
                record.task_id, TaskState.PR_OPEN,
                agent_id=record.agent_id,
                reason=f"harvest {spawn_id}",
                data_dir=data_dir,
            )
        except orchestrator.TransitionError as exc:
            # Another controller may have moved the task to a terminal state.
            # Keep harvest non-throwing and mark this run as failure.
            transition_error = str(exc)
            success = False
            outcome = "failure"
            output_data = {
                **output_data,
                "error": "task_transition_failed",
                "detail": transition_error,
            }
            db.update_spawn(spawn_id, outcome=outcome, data_dir=data_dir)
    else:
        try:
            orchestrator.abort_task(
                record.task_id,
                reason=f"worker failed: {spawn_id}",
                agent_id=record.agent_id,
                data_dir=data_dir,
            )
        except orchestrator.TransitionError as exc:
            # If already terminal, do not crash watcher/CLI.
            transition_error = str(exc)
            output_data = {
                **output_data,
                "error": "task_transition_failed",
                "detail": transition_error,
            }

    # End attempt
    if record.attempt_id:
        db.end_attempt(record.attempt_id, outcome=outcome, data_dir=data_dir)

    # Emit receipt + event
    weaver.append_weave(
        trace_id=spawn_id,
        episode_id=record.episode_id or None,
        data_dir=data_dir,
    )

    events.append_event(
        kind=EventKind.WORKER_DONE,
        agent_id=record.agent_id,
        payload={
            "spawn_id": spawn_id,
            "task_id": record.task_id,
            "outcome": outcome,
            "cost_usd": worker_out.cost_usd,
            "tokens_in": worker_out.tokens_in,
            "tokens_out": worker_out.tokens_out,
            "transition_error": transition_error,
            "verification_command": verify_cmd,
            "verification_passed": verify_passed,
            "verification_summary": _trim_summary(verify_summary),
        },
        data_dir=data_dir,
    )

    # Cleanup worktree
    if cleanup_worktree:
        _cleanup_worktree(record)

    return HarvestResult(
        spawn_id=spawn_id,
        outcome=outcome,
        output_data=output_data,
        cost_usd=worker_out.cost_usd,
        tokens_in=worker_out.tokens_in,
        tokens_out=worker_out.tokens_out,
        verification_command=verify_cmd,
        verification_passed=verify_passed,
        verification_summary=_trim_summary(verify_summary),
    )


def abort(
    spawn_id: str,
    reason: str = "",
    cleanup_worktree: bool = True,
    data_dir: Path | None = None,
) -> SpawnRecord:
    """Abort a running worker: best-effort SIGTERM/SIGKILL, then clean up.

    1. Kill process
    2. Claim finalization atomically (CAS)
    3. Side effects: transition task, end attempt, receipt, event
    4. Remove worktree
    """
    record = _get_spawn(spawn_id, data_dir)
    if record.ended_at:
        raise SpawnError(f"Spawn {spawn_id} already ended ({record.outcome or 'unknown'})")

    # Kill the process (safe to do even if someone else is also aborting)
    _terminate_pid(record.pid)

    # Claim finalization atomically BEFORE side effects.
    now = _now()
    claimed = db.finalize_spawn(spawn_id, ended_at=now, outcome="aborted", data_dir=data_dir)
    if not claimed:
        raise SpawnError(f"Spawn {spawn_id} already finalized (race)")

    # -- Side effects: only the winner of the CAS reaches here --

    # Transition task to ABORTED
    try:
        orchestrator.abort_task(
            record.task_id,
            reason=reason or f"worker aborted: {spawn_id}",
            agent_id=record.agent_id,
            data_dir=data_dir,
        )
    except orchestrator.TransitionError:
        pass  # Already in terminal state

    # End attempt
    if record.attempt_id:
        db.end_attempt(record.attempt_id, outcome="aborted", data_dir=data_dir)

    # Re-read for return value
    record.ended_at = now
    record.outcome = "aborted"

    # Emit receipt + event
    weaver.append_weave(
        trace_id=spawn_id,
        episode_id=record.episode_id or None,
        data_dir=data_dir,
    )

    events.append_event(
        kind=EventKind.WORKER_DONE,
        agent_id=record.agent_id,
        payload={
            "spawn_id": spawn_id,
            "task_id": record.task_id,
            "outcome": "aborted",
            "reason": reason,
        },
        data_dir=data_dir,
    )

    # Cleanup worktree
    if cleanup_worktree:
        _cleanup_worktree(record)

    return record


def list_spawns(
    active_only: bool = False,
    data_dir: Path | None = None,
) -> list[SpawnRecord]:
    """List all spawn records, optionally filtered to active (no ended_at)."""
    rows = db.list_spawns_db(active_only=active_only, data_dir=data_dir)
    return [_row_to_record(r) for r in rows]
