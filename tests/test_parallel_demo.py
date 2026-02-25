"""Parallel orchestration demo -- proves N concurrent tasks produce valid evidence chains.

This is the permanent regression test for multi-task orchestration. Every assertion
here runs in CI on every PR. If any criterion regresses, the test fails loudly.

Go/no-go criteria (all must pass):
  1. Real parallelism: >=2 tasks with overlapping RUNNING windows
  2. Full lifecycle: every task reaches a terminal state (MERGED or ABORTED)
  3. Receipt completeness: one ASSAY_RECEIPT per terminal transition, zero gaps
  4. Weave integrity: weaver.verify_weave() exits clean
  5. Watchdog intervention: >=1 task aborted by watchdog (not manually)
  6. Cleanup: zero orphan spawns (alpha gate check #6)
  7. Determinism: same seed produces same terminal states
  8. Wall time: total < 60s for CI profile
"""

from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from agentmesh import db, events, orchestrator, spawner, watchdog, weaver
from agentmesh.alpha_gate import build_alpha_gate_report
from agentmesh.models import Agent, AgentStatus, EventKind, TaskState

# ---------------------------------------------------------------------------
# Report schema
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "demo_report_v1"


@dataclass
class DemoReport:
    schema_version: str = SCHEMA_VERSION
    seed: int = 0
    task_count: int = 0
    merged_count: int = 0
    aborted_count: int = 0
    wall_time_seconds: float = 0.0
    concurrency_peak: int = 0
    overlap_pairs: int = 0
    receipt_chain_complete: bool = False
    weave_chain_intact: bool = False
    watchdog_intervened: bool = False
    alpha_gate_pass: bool = False
    task_results: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Task scenario
# ---------------------------------------------------------------------------

@dataclass
class TaskScenario:
    """Deterministic scenario for one worker task."""

    title: str
    delay_seconds: float  # simulated work time per state transition
    should_fail: bool = False  # if True, this task will be killed by watchdog
    terminal_state: TaskState | None = None  # filled after execution


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Fresh DB in a temp dir."""
    db.init_db(tmp_path)
    return tmp_path


@pytest.fixture
def agents(data_dir: Path) -> list[Agent]:
    """Register worker agents."""
    out = []
    for i in range(5):
        a = Agent(agent_id=f"worker_{i}", cwd="/tmp")
        db.register_agent(a, data_dir)
        out.append(a)
    return out


# ---------------------------------------------------------------------------
# Preflight check
# ---------------------------------------------------------------------------

def _preflight_check(data_dir: Path) -> None:
    """Assert the DB is in a clean state before the demo run."""
    tasks = db.list_tasks(data_dir=data_dir, limit=5000)
    active = [t for t in tasks if t.state not in orchestrator.TERMINAL_STATES]
    assert len(active) == 0, f"Preflight: found {len(active)} non-terminal tasks"

    spawns = db.list_spawns_db(active_only=True, data_dir=data_dir)
    assert len(spawns) == 0, f"Preflight: found {len(spawns)} active spawns"


# ---------------------------------------------------------------------------
# Worker: drives a single task through the state machine
# ---------------------------------------------------------------------------

def _run_worker(
    task_id: str,
    agent_id: str,
    scenario: TaskScenario,
    data_dir: Path,
    running_windows: dict[str, tuple[float, float]],
) -> TaskScenario:
    """Walk a task through PLANNED -> ... -> MERGED, recording timing.

    If scenario.should_fail is True, the task stays in RUNNING and will be
    aborted by the watchdog (stale heartbeat) after the test calls scan().
    """
    delay = scenario.delay_seconds

    # PLANNED -> ASSIGNED
    orchestrator.assign_task(task_id, agent_id, branch=f"feat/{task_id}", data_dir=data_dir)
    time.sleep(delay * 0.1)

    # ASSIGNED -> RUNNING
    orchestrator.transition_task(task_id, TaskState.RUNNING, agent_id=agent_id, data_dir=data_dir)
    running_start = time.monotonic()

    if scenario.should_fail:
        # Simulate a stalled worker: stay in RUNNING, never progress.
        # The watchdog will catch the stale heartbeat and abort this task.
        time.sleep(delay * 0.5)
        running_end = time.monotonic()
        running_windows[task_id] = (running_start, running_end)
        scenario.terminal_state = None  # will be set by watchdog
        return scenario

    # Simulate work
    time.sleep(delay * 0.3)

    # RUNNING -> PR_OPEN
    orchestrator.transition_task(task_id, TaskState.PR_OPEN, agent_id=agent_id, data_dir=data_dir)
    time.sleep(delay * 0.1)

    # PR_OPEN -> CI_PASS
    orchestrator.transition_task(task_id, TaskState.CI_PASS, agent_id=agent_id, data_dir=data_dir)
    time.sleep(delay * 0.1)

    # CI_PASS -> REVIEW_PASS
    orchestrator.transition_task(task_id, TaskState.REVIEW_PASS, agent_id=agent_id, data_dir=data_dir)
    time.sleep(delay * 0.1)

    running_end = time.monotonic()
    running_windows[task_id] = (running_start, running_end)

    # REVIEW_PASS -> MERGED
    orchestrator.complete_task(task_id, agent_id=agent_id, data_dir=data_dir)
    scenario.terminal_state = TaskState.MERGED
    return scenario


# ---------------------------------------------------------------------------
# Overlap detection
# ---------------------------------------------------------------------------

def _count_overlap_pairs(windows: dict[str, tuple[float, float]]) -> int:
    """Count how many pairs of tasks had overlapping RUNNING windows."""
    ids = list(windows.keys())
    count = 0
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            start_a, end_a = windows[ids[i]]
            start_b, end_b = windows[ids[j]]
            # Two intervals overlap if one starts before the other ends
            if start_a < end_b and start_b < end_a:
                count += 1
    return count


# ---------------------------------------------------------------------------
# Core demo test
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seed", [42])
def test_parallel_demo(data_dir: Path, agents: list[Agent], seed: int) -> None:
    """5 tasks run concurrently: 4 merge successfully, 1 is aborted by watchdog."""
    wall_start = time.monotonic()
    rng = random.Random(seed)

    # -- Preflight --
    _preflight_check(data_dir)

    # -- Build deterministic scenarios --
    scenarios: list[TaskScenario] = []
    for i in range(5):
        scenarios.append(TaskScenario(
            title=f"Demo task {i}",
            delay_seconds=rng.uniform(0.02, 0.08),  # fast for CI
            should_fail=(i == 2),  # task index 2 will stall -> watchdog abort
        ))

    # -- Create all tasks --
    tasks = []
    for i, sc in enumerate(scenarios):
        task = orchestrator.create_task(sc.title, description=f"seed={seed} idx={i}", data_dir=data_dir)
        tasks.append(task)

    # -- Run workers concurrently --
    running_windows: dict[str, tuple[float, float]] = {}

    with patch("agentmesh.assay_bridge.shutil.which", return_value=None):
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {}
            for i, (task, sc) in enumerate(zip(tasks, scenarios)):
                fut = pool.submit(
                    _run_worker,
                    task.task_id,
                    agents[i].agent_id,
                    sc,
                    data_dir,
                    running_windows,
                )
                futures[fut] = (task, sc)

            for fut in as_completed(futures):
                fut.result()  # propagate exceptions

        # -- Watchdog pass: detect the stalled worker --
        # Mark the stalled agent as stale so watchdog picks it up.
        stalled_agent_id = agents[2].agent_id
        stale_ts = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
        db.update_heartbeat(stalled_agent_id, ts=stale_ts, data_dir=data_dir)

        wd_result = watchdog.scan(stale_threshold_s=300, data_dir=data_dir)

    wall_seconds = time.monotonic() - wall_start

    # -- Re-read final task states --
    final_tasks = db.list_tasks(data_dir=data_dir, limit=100)
    demo_task_ids = {t.task_id for t in tasks}
    demo_tasks = [t for t in final_tasks if t.task_id in demo_task_ids]

    merged = [t for t in demo_tasks if t.state == TaskState.MERGED]
    aborted = [t for t in demo_tasks if t.state == TaskState.ABORTED]
    non_terminal = [t for t in demo_tasks if t.state not in orchestrator.TERMINAL_STATES]

    # ===================================================================
    # GO/NO-GO CRITERIA
    # ===================================================================

    # 1. Real parallelism: >=2 tasks with overlapping RUNNING windows
    overlap_pairs = _count_overlap_pairs(running_windows)
    assert overlap_pairs >= 2, (
        f"Criterion 1 FAILED: only {overlap_pairs} overlap pairs, need >=2. "
        f"Windows: {running_windows}"
    )

    # 2. Full lifecycle: every task reaches a terminal state
    assert len(non_terminal) == 0, (
        f"Criterion 2 FAILED: {len(non_terminal)} tasks not in terminal state: "
        f"{[(t.task_id, t.state.value) for t in non_terminal]}"
    )

    # 3. Receipt completeness: one ASSAY_RECEIPT per terminal transition
    all_events = events.read_events(data_dir)
    receipts = [e for e in all_events if e.kind == EventKind.ASSAY_RECEIPT]
    receipt_task_ids = {r.payload["task_id"] for r in receipts}
    terminal_task_ids = {t.task_id for t in demo_tasks if t.state in orchestrator.TERMINAL_STATES}
    missing_receipts = terminal_task_ids - receipt_task_ids
    assert len(missing_receipts) == 0, (
        f"Criterion 3 FAILED: missing ASSAY_RECEIPT for tasks: {missing_receipts}"
    )
    # No duplicate receipts per task
    for task_id in terminal_task_ids:
        task_receipts = [r for r in receipts if r.payload["task_id"] == task_id]
        assert len(task_receipts) == 1, (
            f"Criterion 3 FAILED: task {task_id} has {len(task_receipts)} receipts, expected 1"
        )

    # 4. Weave integrity
    weave_ok, weave_err = weaver.verify_weave(data_dir=data_dir)
    assert weave_ok, f"Criterion 4 FAILED: weave verification error: {weave_err}"

    # 5. Watchdog intervention: >=1 task aborted by watchdog
    assert len(wd_result.aborted_tasks) >= 1, (
        f"Criterion 5 FAILED: watchdog aborted {len(wd_result.aborted_tasks)} tasks, need >=1"
    )
    # The specific stalled task should be among the aborted
    stalled_task_id = tasks[2].task_id
    assert stalled_task_id in wd_result.aborted_tasks, (
        f"Criterion 5 FAILED: stalled task {stalled_task_id} not in watchdog aborted list"
    )

    # 6. Cleanup: no orphan spawns (via alpha gate check)
    # We skip witness_verified since this is a local demo (no CI log)
    gate_report = build_alpha_gate_report(
        data_dir,
        require_witness_verified=False,
    )
    spawn_loss = gate_report["checks"]["no_orphan_finalization_loss"]
    assert spawn_loss["pass"], (
        f"Criterion 6 FAILED: orphan spawns detected: {spawn_loss}"
    )

    # 7. Determinism: expected terminal state distribution
    assert len(merged) == 4, (
        f"Criterion 7 FAILED: expected 4 merged, got {len(merged)}"
    )
    assert len(aborted) == 1, (
        f"Criterion 7 FAILED: expected 1 aborted, got {len(aborted)}"
    )
    assert aborted[0].task_id == stalled_task_id, (
        f"Criterion 7 FAILED: wrong task aborted. Expected {stalled_task_id}, "
        f"got {aborted[0].task_id}"
    )

    # 8. Wall time: total < 60s for CI profile
    assert wall_seconds < 60, (
        f"Criterion 8 FAILED: demo took {wall_seconds:.1f}s, budget is 60s"
    )

    # ===================================================================
    # Event chain hash integrity (both chains)
    # ===================================================================
    event_chain_ok, event_chain_err = events.verify_chain(data_dir)
    assert event_chain_ok, f"Event chain integrity FAILED: {event_chain_err}"

    # ===================================================================
    # Alpha gate (composite check)
    # ===================================================================
    # The gate includes: merged_task_count, weave_chain_intact,
    # full_transition_receipts, watchdog_handled_event, no_orphan_finalization_loss.
    # witness_verified is disabled since this is a local run.
    assert gate_report["checks"]["merged_task_count"]["pass"], (
        f"Alpha gate: merged_task_count failed: {gate_report['checks']['merged_task_count']}"
    )
    assert gate_report["checks"]["weave_chain_intact"]["pass"], (
        f"Alpha gate: weave_chain_intact failed: {gate_report['checks']['weave_chain_intact']}"
    )
    assert gate_report["checks"]["full_transition_receipts"]["pass"], (
        f"Alpha gate: full_transition_receipts failed: {gate_report['checks']['full_transition_receipts']}"
    )
    assert gate_report["checks"]["watchdog_handled_event"]["pass"], (
        f"Alpha gate: watchdog_handled_event failed: {gate_report['checks']['watchdog_handled_event']}"
    )

    # ===================================================================
    # Weave sequence monotonicity (no gaps across concurrent appends)
    # ===================================================================
    weave_events = db.list_weave_events(data_dir)
    sequences = [e.sequence_id for e in weave_events]
    assert sequences == list(range(1, len(sequences) + 1)), (
        f"Weave sequence not monotonic: gaps at {_find_gaps(sequences)}"
    )

    # ===================================================================
    # Build structured report (for artifact consumers)
    # ===================================================================
    report = DemoReport(
        seed=seed,
        task_count=len(demo_tasks),
        merged_count=len(merged),
        aborted_count=len(aborted),
        wall_time_seconds=round(wall_seconds, 2),
        concurrency_peak=len(running_windows),
        overlap_pairs=overlap_pairs,
        receipt_chain_complete=(len(missing_receipts) == 0),
        weave_chain_intact=weave_ok,
        watchdog_intervened=(len(wd_result.aborted_tasks) >= 1),
        alpha_gate_pass=gate_report.get("overall_pass", False),
        task_results=[
            {
                "task_id": t.task_id,
                "title": t.title,
                "terminal_state": t.state.value,
            }
            for t in demo_tasks
        ],
    )
    # The report is available for inspection in test output
    print(f"\n--- Demo Report (schema={report.schema_version}) ---")
    print(f"  Seed: {report.seed}")
    print(f"  Tasks: {report.task_count} ({report.merged_count} merged, {report.aborted_count} aborted)")
    print(f"  Wall time: {report.wall_time_seconds}s")
    print(f"  Concurrency peak: {report.concurrency_peak}")
    print(f"  Overlap pairs: {report.overlap_pairs}")
    print(f"  Receipt chain: {'complete' if report.receipt_chain_complete else 'INCOMPLETE'}")
    print(f"  Weave chain: {'intact' if report.weave_chain_intact else 'BROKEN'}")
    print(f"  Watchdog: {'intervened' if report.watchdog_intervened else 'SILENT'}")
    print(f"  Alpha gate: {'PASS' if report.alpha_gate_pass else 'FAIL'}")


# ---------------------------------------------------------------------------
# Supporting tests
# ---------------------------------------------------------------------------

def test_overlap_detection_logic() -> None:
    """Unit test for the overlap counting helper."""
    windows = {
        "a": (0.0, 3.0),
        "b": (1.0, 4.0),  # overlaps with a
        "c": (5.0, 7.0),  # no overlap with a or b
    }
    assert _count_overlap_pairs(windows) == 1

    windows2 = {
        "a": (0.0, 5.0),
        "b": (1.0, 6.0),
        "c": (2.0, 7.0),
    }
    # a-b, a-c, b-c = 3 pairs
    assert _count_overlap_pairs(windows2) == 3


def test_preflight_on_dirty_state(data_dir: Path, agents: list[Agent]) -> None:
    """Preflight check must fail if there are non-terminal tasks."""
    with patch("agentmesh.assay_bridge.shutil.which", return_value=None):
        task = orchestrator.create_task("Leftover", data_dir=data_dir)
        orchestrator.assign_task(task.task_id, agents[0].agent_id, data_dir=data_dir)
        orchestrator.transition_task(task.task_id, TaskState.RUNNING, data_dir=data_dir)

    with pytest.raises(AssertionError, match="non-terminal tasks"):
        _preflight_check(data_dir)


def test_report_schema_version() -> None:
    """Schema version must be stable for downstream consumers."""
    report = DemoReport()
    assert report.schema_version == "demo_report_v1"


def test_deterministic_scenarios() -> None:
    """Same seed produces same scenario parameters."""
    rng1 = random.Random(42)
    delays1 = [rng1.uniform(0.02, 0.08) for _ in range(5)]

    rng2 = random.Random(42)
    delays2 = [rng2.uniform(0.02, 0.08) for _ in range(5)]

    assert delays1 == delays2


class _FakePopen:
    """Minimal subprocess.Popen stand-in for spawner integration tests."""

    def __init__(self, *args, **kwargs):
        self.pid = 77777
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def terminate(self):
        return None

    def kill(self):
        return None


def _init_git_repo(tmp_path: Path) -> Path:
    """Create a git repo with one commit (required for worktree operations)."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "demo@example.com"],
        cwd=str(repo),
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Demo"],
        cwd=str(repo),
        capture_output=True,
        check=True,
    )
    (repo / "README.md").write_text("demo\n")
    subprocess.run(["git", "add", "README.md"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True, check=True)
    return repo


def test_no_orphan_check_non_vacuous_with_spawner(data_dir: Path, agents: list[Agent], tmp_path: Path) -> None:
    """Alpha gate no-orphan check must pass on real finalized spawn rows.

    This test makes criterion #6 non-vacuous by creating real spawns via
    spawner.spawn and finalizing them via harvest + abort.
    """
    repo = _init_git_repo(tmp_path)

    # Build two tasks and assign both so spawner can run.
    task_ok = orchestrator.create_task("spawn success", data_dir=data_dir)
    task_abort = orchestrator.create_task("spawn abort", data_dir=data_dir)
    orchestrator.assign_task(task_ok.task_id, agents[0].agent_id, branch="feat/spawn-ok", data_dir=data_dir)
    orchestrator.assign_task(task_abort.task_id, agents[1].agent_id, branch="feat/spawn-abort", data_dir=data_dir)

    with patch("subprocess.Popen", _FakePopen):
        with patch.object(spawner, "create_worktree", return_value=(True, "")):
            ok_record = spawner.spawn(
                task_id=task_ok.task_id,
                agent_id=agents[0].agent_id,
                repo_cwd=str(repo),
                data_dir=data_dir,
            )
            abort_record = spawner.spawn(
                task_id=task_abort.task_id,
                agent_id=agents[1].agent_id,
                repo_cwd=str(repo),
                data_dir=data_dir,
            )

    # Make harvest success deterministic.
    out = Path(ok_record.output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text('{"result":"ok","cost_usd":0.01}')

    with patch("os.kill", side_effect=ProcessLookupError):
        with patch.object(spawner, "remove_worktree", return_value=(True, "")):
            harvest_result = spawner.harvest(ok_record.spawn_id, data_dir=data_dir)

    assert harvest_result.outcome == "success"

    # Finalize second spawn via abort path.
    with patch("os.kill"):
        with patch.object(spawner, "remove_worktree", return_value=(True, "")):
            abort_result = spawner.abort(abort_record.spawn_id, reason="demo abort", data_dir=data_dir)

    assert abort_result.outcome == "aborted"

    gate_report = build_alpha_gate_report(
        data_dir,
        require_witness_verified=False,
    )
    spawn_loss = gate_report["checks"]["no_orphan_finalization_loss"]

    # Non-vacuous assertion: this check must have evaluated actual spawn rows.
    assert gate_report["summary"]["spawns_total"] >= 2
    assert spawn_loss["pass"], f"Expected no orphan/finalization loss, got: {spawn_loss}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_gaps(sequences: list[int]) -> list[int]:
    """Find missing integers in a sequence starting from 1."""
    if not sequences:
        return []
    expected = set(range(1, max(sequences) + 1))
    return sorted(expected - set(sequences))
