"""CLI tests for the orchestrator sub-app."""

from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import patch

from typer.testing import CliRunner

from agentmesh import db, events
from agentmesh.cli import app
from agentmesh.models import Agent, EventKind
from agentmesh import orch_control, orchestrator
from agentmesh.models import TaskState

runner = CliRunner()


def _invoke(args: list[str], tmp_path):
    return runner.invoke(app, ["--data-dir", str(tmp_path)] + args)


def _setup(tmp_path):
    db.init_db(tmp_path)
    a = Agent(agent_id="agent_cli", cwd="/tmp")
    db.register_agent(a, tmp_path)


# -- orch create --

def test_orch_create(tmp_path):
    _setup(tmp_path)
    result = _invoke(["orch", "create", "--title", "Fix bug"], tmp_path)
    assert result.exit_code == 0
    assert "task_" in result.output
    assert "planned" in result.output


def test_orch_create_json(tmp_path):
    _setup(tmp_path)
    result = _invoke(["orch", "create", "--title", "JSON task", "--json"], tmp_path)
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["state"] == "planned"
    assert data["task_id"].startswith("task_")


# -- orch list --

def test_orch_list(tmp_path):
    _setup(tmp_path)
    _invoke(["orch", "create", "--title", "T1"], tmp_path)
    _invoke(["orch", "create", "--title", "T2"], tmp_path)
    result = _invoke(["orch", "list"], tmp_path)
    assert result.exit_code == 0
    assert "T1" in result.output
    assert "T2" in result.output


# -- orch assign + show --

def test_orch_assign_and_show(tmp_path):
    _setup(tmp_path)
    create_result = _invoke(["orch", "create", "--title", "Assign me", "--json"], tmp_path)
    task_id = json.loads(create_result.output)["task_id"]

    assign_result = _invoke(["orch", "assign", task_id, "--agent", "agent_cli", "--branch", "feat/x"], tmp_path)
    assert assign_result.exit_code == 0
    assert "assigned" in assign_result.output

    show_result = _invoke(["orch", "show", task_id, "--json"], tmp_path)
    assert show_result.exit_code == 0
    data = json.loads(show_result.output)
    assert data["state"] == "assigned"
    assert data["assigned_agent_id"] == "agent_cli"
    assert len(data["attempts"]) == 1


# -- orch advance + abort --

def test_orch_advance_and_abort(tmp_path):
    _setup(tmp_path)
    create_result = _invoke(["orch", "create", "--title", "Advance me", "--json"], tmp_path)
    task_id = json.loads(create_result.output)["task_id"]

    _invoke(["orch", "assign", task_id, "--agent", "agent_cli"], tmp_path)

    advance_result = _invoke(["orch", "advance", task_id, "--to", "running"], tmp_path)
    assert advance_result.exit_code == 0
    assert "running" in advance_result.output

    abort_result = _invoke(["orch", "abort", task_id, "--reason", "test abort"], tmp_path)
    assert abort_result.exit_code == 0
    assert "Aborted" in abort_result.output


def _advance_to_review_pass(task_id: str, tmp_path) -> None:
    for state in ("running", "pr_open", "ci_pass", "review_pass"):
        args = ["orch", "advance", task_id, "--to", state]
        if state == "pr_open":
            args.extend(["--pr-url", "https://github.com/test/repo/pull/999"])
        res = _invoke(args, tmp_path)
        assert res.exit_code == 0, res.output


def test_orch_advance_merged_closes_attempt_and_emits_assay_receipt(tmp_path):
    _setup(tmp_path)
    create_result = _invoke(["orch", "create", "--title", "Merge terminal path", "--json"], tmp_path)
    task_id = json.loads(create_result.output)["task_id"]

    _invoke(["orch", "assign", task_id, "--agent", "agent_cli"], tmp_path)
    _advance_to_review_pass(task_id, tmp_path)

    with patch("agentmesh.assay_bridge.shutil.which", return_value=None):
        merged = _invoke(["orch", "advance", task_id, "--to", "merged", "--json"], tmp_path)

    assert merged.exit_code == 0
    assert json.loads(merged.output)["state"] == "merged"

    show = _invoke(["orch", "show", task_id, "--json"], tmp_path)
    payload = json.loads(show.output)
    assert payload["attempts"]
    assert payload["attempts"][-1]["ended_at"] != ""
    assert payload["attempts"][-1]["outcome"] == "success"

    evts = events.read_events(tmp_path)
    receipts = [
        e
        for e in evts
        if e.kind == EventKind.ASSAY_RECEIPT and e.payload.get("task_id") == task_id
    ]
    assert len(receipts) == 1
    assert receipts[0].payload["terminal_state"] == "MERGED"


def test_orch_advance_aborted_emits_assay_receipt(tmp_path):
    _setup(tmp_path)
    create_result = _invoke(["orch", "create", "--title", "Abort terminal path", "--json"], tmp_path)
    task_id = json.loads(create_result.output)["task_id"]

    _invoke(["orch", "assign", task_id, "--agent", "agent_cli"], tmp_path)
    _invoke(["orch", "advance", task_id, "--to", "running"], tmp_path)

    with patch("agentmesh.assay_bridge.shutil.which", return_value=None):
        aborted = _invoke(
            ["orch", "advance", task_id, "--to", "aborted", "--reason", "manual stop", "--json"],
            tmp_path,
        )

    assert aborted.exit_code == 0
    assert json.loads(aborted.output)["state"] == "aborted"

    evts = events.read_events(tmp_path)
    receipts = [
        e
        for e in evts
        if e.kind == EventKind.ASSAY_RECEIPT and e.payload.get("task_id") == task_id
    ]
    assert len(receipts) == 1
    assert receipts[0].payload["terminal_state"] == "ABORTED"


# -- invalid transition --

def test_orch_advance_invalid(tmp_path):
    _setup(tmp_path)
    create_result = _invoke(["orch", "create", "--title", "Bad transition", "--json"], tmp_path)
    task_id = json.loads(create_result.output)["task_id"]

    result = _invoke(["orch", "advance", task_id, "--to", "running"], tmp_path)
    assert result.exit_code == 1
    assert "Cannot transition" in result.output


def test_orch_create_json_fails_with_lock_conflict(tmp_path):
    _setup(tmp_path)
    owner = orch_control.make_owner("holder")
    ok, _, _ = orch_control.acquire_lease(owner=owner, data_dir=tmp_path, ttl_s=3600)
    assert ok
    try:
        result = _invoke(["orch", "create", "--title", "Blocked", "--json"], tmp_path)
    finally:
        orch_control.release_lease(owner, data_dir=tmp_path)

    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["error"] == "orchestration_lock_conflict"
    assert data["resource"] == "LOCK:orchestration"
    assert data["holders"]


def test_orch_freeze_toggle(tmp_path):
    _setup(tmp_path)
    on = _invoke(["orch", "freeze"], tmp_path)
    assert on.exit_code == 0
    assert orch_control.is_frozen(tmp_path) is True

    off = _invoke(["orch", "freeze", "--off"], tmp_path)
    assert off.exit_code == 0
    assert orch_control.is_frozen(tmp_path) is False


def test_merge_lock_blocks_transition_to_merged(tmp_path):
    _setup(tmp_path)
    task = orchestrator.create_task("merge lock", data_dir=tmp_path)
    orchestrator.assign_task(task.task_id, "agent_cli", data_dir=tmp_path)
    orchestrator.transition_task(task.task_id, TaskState.RUNNING, data_dir=tmp_path)
    orchestrator.transition_task(task.task_id, TaskState.PR_OPEN, data_dir=tmp_path)
    orchestrator.transition_task(task.task_id, TaskState.CI_PASS, data_dir=tmp_path)
    orchestrator.transition_task(task.task_id, TaskState.REVIEW_PASS, data_dir=tmp_path)

    lock = _invoke(["orch", "lock-merges"], tmp_path)
    assert lock.exit_code == 0

    blocked = _invoke(["orch", "advance", task.task_id, "--to", "merged"], tmp_path)
    assert blocked.exit_code == 1
    assert "merge transitions are locked" in blocked.output


def test_orch_abort_all_clears_lock(tmp_path):
    _setup(tmp_path)
    owner = orch_control.make_owner("holder")
    ok, _, _ = orch_control.acquire_lease(owner=owner, data_dir=tmp_path, ttl_s=3600)
    assert ok
    result = _invoke(["orch", "abort-all", "--json"], tmp_path)
    assert result.exit_code == 0
    assert orch_control.lease_holders(tmp_path) == []


def test_orch_watch_once_json(tmp_path):
    _setup(tmp_path)
    events.append_event(
        EventKind.TASK_TRANSITION,
        payload={"task_id": "task_x", "from_state": "planned", "to_state": "assigned"},
        data_dir=tmp_path,
    )
    result = _invoke(["orch", "watch", "--once", "--json"], tmp_path)
    assert result.exit_code == 0
    lines = [ln for ln in result.output.splitlines() if ln.strip()]
    assert lines
    payload = json.loads(lines[0])
    assert payload["kind"] == "TASK_TRANSITION"


def test_orch_lease_renew_json(tmp_path):
    _setup(tmp_path)
    owner = "orchctl_test_owner"
    # First acquire
    ok, _, _ = orch_control.acquire_lease(owner=owner, data_dir=tmp_path, ttl_s=60)
    assert ok
    # Then renew via CLI
    result = _invoke(["orch", "lease-renew", "--owner", owner, "--ttl", "120", "--json"], tmp_path)
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["owner"] == owner
    assert payload["ttl_s"] == 120


def test_orch_run_json_single_iteration(tmp_path):
    _setup(tmp_path)
    result = _invoke(["orch", "run", "--max-iterations", "1", "--interval", "0", "--json"], tmp_path)
    assert result.exit_code == 0
    lines = [ln for ln in result.output.splitlines() if ln.strip()]
    assert lines
    payload = json.loads(lines[0])
    assert payload["loop"] == 1
    assert "clean" in payload


def test_orch_run_fails_on_lease_renew_failure(tmp_path):
    _setup(tmp_path)

    @contextmanager
    def _fake_lease(*args, **kwargs):
        yield "owner_x", {"renew_ok": False, "error": "boom"}

    with patch("agentmesh.cli._orchestrator_lease_heartbeat", _fake_lease):
        result = _invoke(["orch", "run", "--max-iterations", "1", "--json"], tmp_path)
    assert result.exit_code == 1
    payload = json.loads(result.output.splitlines()[0])
    assert payload["error"] == "lease_renew_failed"


def test_orch_create_with_dependency_blocks_assignment_until_ready(tmp_path):
    _setup(tmp_path)
    dep = _invoke(["orch", "create", "--title", "dep", "--json"], tmp_path)
    dep_id = json.loads(dep.output)["task_id"]
    _invoke(["orch", "assign", dep_id, "--agent", "agent_cli"], tmp_path)
    _invoke(["orch", "advance", dep_id, "--to", "running"], tmp_path)

    main = _invoke(
        ["orch", "create", "--title", "main", "--depends-on", dep_id, "--json"],
        tmp_path,
    )
    assert main.exit_code == 0
    main_payload = json.loads(main.output)
    main_id = main_payload["task_id"]
    assert main_payload["depends_on"] == [dep_id]

    blocked = _invoke(["orch", "assign", main_id, "--agent", "agent_cli"], tmp_path)
    assert blocked.exit_code == 1
    assert "unresolved dependencies" in blocked.output

    _invoke(["orch", "advance", dep_id, "--to", "pr_open"], tmp_path)
    assigned = _invoke(["orch", "assign", main_id, "--agent", "agent_cli"], tmp_path)
    assert assigned.exit_code == 0
    assert "assigned" in assigned.output


def test_orch_depends_detects_cycle(tmp_path):
    _setup(tmp_path)
    a = _invoke(["orch", "create", "--title", "a", "--json"], tmp_path)
    b = _invoke(["orch", "create", "--title", "b", "--json"], tmp_path)
    a_id = json.loads(a.output)["task_id"]
    b_id = json.loads(b.output)["task_id"]

    ok = _invoke(["orch", "depends", a_id, "--on", b_id], tmp_path)
    assert ok.exit_code == 0
    bad = _invoke(["orch", "depends", b_id, "--on", a_id], tmp_path)
    assert bad.exit_code == 1
    assert "cycle detected" in bad.output
