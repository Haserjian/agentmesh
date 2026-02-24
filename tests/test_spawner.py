"""Tests for git worktree helpers and the spawner module."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from agentmesh import db, events, orch_control, orchestrator
from agentmesh.gitbridge import create_worktree, list_worktrees, remove_worktree
from agentmesh.models import Agent, EventKind, TaskState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _init_repo(tmp_path: Path) -> Path:
    """Create a git repo with one commit so worktrees can branch."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(repo), capture_output=True, check=True)
    (repo / "init.txt").write_text("init\n")
    subprocess.run(["git", "add", "init.txt"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True, check=True)
    return repo


# ---------------------------------------------------------------------------
# Worktree helpers (Commit 1)
# ---------------------------------------------------------------------------

def test_create_and_list_worktree(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    wt_path = str(tmp_path / "wt1")

    ok, err = create_worktree("feat-a", wt_path, cwd=str(repo))
    assert ok, f"create_worktree failed: {err}"
    assert Path(wt_path).is_dir()
    assert (Path(wt_path) / "init.txt").exists()

    trees = list_worktrees(cwd=str(repo))
    paths = [t["path"] for t in trees]
    assert wt_path in paths


def test_remove_worktree(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    wt_path = str(tmp_path / "wt2")
    create_worktree("feat-b", wt_path, cwd=str(repo))

    ok, err = remove_worktree(wt_path, cwd=str(repo))
    assert ok, f"remove_worktree failed: {err}"
    assert not Path(wt_path).exists()


def test_remove_worktree_without_cwd_prunes_registration(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    wt_path = str(repo / ".worktrees" / "wt-prune")
    create_worktree("feat-prune", wt_path, cwd=str(repo))

    old_cwd = Path.cwd()
    try:
        # Simulate cleanup invoked from outside any git repo.
        import os
        os.chdir("/")
        ok, err = remove_worktree(wt_path, cwd=None, force=True)
    finally:
        os.chdir(str(old_cwd))

    assert ok, f"remove_worktree failed: {err}"
    paths = [str(Path(t["path"]).resolve()) for t in list_worktrees(cwd=str(repo))]
    assert str(Path(wt_path).resolve()) not in paths

    # Re-create should succeed if stale registration was pruned.
    ok2, err2 = create_worktree("feat-prune", wt_path, cwd=str(repo))
    assert ok2, f"re-create failed due to stale worktree registration: {err2}"


def test_create_worktree_existing_branch(tmp_path: Path) -> None:
    """Creating a worktree for an already-existing branch works."""
    repo = _init_repo(tmp_path)
    subprocess.run(["git", "branch", "existing-br"], cwd=str(repo), capture_output=True, check=True)
    wt_path = str(tmp_path / "wt3")
    ok, err = create_worktree("existing-br", wt_path, cwd=str(repo))
    assert ok, f"create_worktree failed: {err}"
    assert Path(wt_path).is_dir()


# ---------------------------------------------------------------------------
# Spawner module (Commit 2) -- added below after spawner.py exists
# ---------------------------------------------------------------------------

def _setup_orch(tmp_path: Path) -> Path:
    """Init DB and register an agent. Returns data_dir."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db.init_db(data_dir)
    a = Agent(agent_id="agent_spawn", cwd="/tmp")
    db.register_agent(a, data_dir)
    return data_dir


def _make_assigned_task(
    data_dir: Path,
    branch: str = "feat/spawn",
    meta: dict | None = None,
) -> str:
    """Create and assign a task, returning task_id."""
    task = orchestrator.create_task("Test spawn", description="test", meta=meta, data_dir=data_dir)
    orchestrator.assign_task(task.task_id, "agent_spawn", branch=branch, data_dir=data_dir)
    return task.task_id


class FakePopen:
    """Minimal mock for subprocess.Popen."""

    def __init__(self, *args, **kwargs):
        self.pid = 99999
        self.returncode = None
        self._poll_count = 0

    def poll(self):
        self._poll_count += 1
        if self._poll_count > 1:
            self.returncode = 0
            return 0
        return None

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def terminate(self):
        pass

    def kill(self):
        pass


def test_spawn_creates_record(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    data_dir = _setup_orch(tmp_path)
    task_id = _make_assigned_task(data_dir, branch="feat/spawn-test")

    from agentmesh import spawner

    with patch("subprocess.Popen", FakePopen):
        with patch.object(spawner, "create_worktree", return_value=(True, "")):
            record = spawner.spawn(
                task_id=task_id,
                agent_id="agent_spawn",
                repo_cwd=str(repo),
                data_dir=data_dir,
            )

    assert record.spawn_id.startswith("spawn_")
    assert record.task_id == task_id
    assert record.pid == 99999
    assert record.outcome == ""

    # Task should be RUNNING now
    t = db.get_task(task_id, data_dir)
    assert t.state == TaskState.RUNNING


def test_spawn_rejects_non_assigned(tmp_path: Path) -> None:
    data_dir = _setup_orch(tmp_path)
    task = orchestrator.create_task("Not assigned", data_dir=data_dir)

    from agentmesh import spawner

    with pytest.raises(spawner.SpawnError, match="not in ASSIGNED state"):
        spawner.spawn(task.task_id, "agent_spawn", "/tmp", data_dir=data_dir)


def test_spawn_rejects_when_frozen(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    data_dir = _setup_orch(tmp_path)
    task_id = _make_assigned_task(data_dir, branch="feat/frozen")

    owner = orch_control.make_owner("freeze")
    orch_control.set_frozen(True, owner=owner, data_dir=data_dir, reason="maintenance")

    from agentmesh import spawner

    with pytest.raises(spawner.SpawnError, match="frozen"):
        spawner.spawn(task_id, "agent_spawn", str(repo), data_dir=data_dir)

    orch_control.set_frozen(False, owner=owner, data_dir=data_dir)


def test_spawn_start_failure_cleans_up_and_raises(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    data_dir = _setup_orch(tmp_path)
    task_id = _make_assigned_task(data_dir)

    from agentmesh import spawner

    with patch.object(spawner, "create_worktree", return_value=(True, "")):
        with patch(
            "agentmesh.spawner.subprocess.Popen",
            side_effect=FileNotFoundError("claude not found"),
        ):
            with pytest.raises(spawner.SpawnError, match="Failed to start worker process"):
                spawner.spawn(task_id, "agent_spawn", str(repo), data_dir=data_dir)

    # Failed spawn should not leave an active process record.
    assert spawner.list_spawns(data_dir=data_dir) == []
    # Task should still be ASSIGNED (transition to RUNNING never happened).
    t = db.get_task(task_id, data_dir)
    assert t.state == TaskState.ASSIGNED


def test_spawn_transition_failure_terminates_process_and_cleans(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    data_dir = _setup_orch(tmp_path)
    task_id = _make_assigned_task(data_dir)

    from agentmesh import spawner

    with patch("subprocess.Popen", FakePopen):
        with patch.object(spawner, "create_worktree", return_value=(True, "")):
            with patch.object(
                spawner.orchestrator,
                "transition_task",
                side_effect=spawner.orchestrator.TransitionError("bad transition"),
            ):
                with patch.object(spawner, "remove_worktree", return_value=(True, "")) as rm:
                    with patch("os.kill") as kill:
                        with pytest.raises(
                            spawner.SpawnError, match="Failed to transition task to RUNNING",
                        ):
                            spawner.spawn(task_id, "agent_spawn", str(repo), data_dir=data_dir)
    assert kill.call_count >= 1
    assert rm.call_count >= 1
    assert spawner.list_spawns(data_dir=data_dir) == []
    t = db.get_task(task_id, data_dir)
    assert t.state == TaskState.ASSIGNED


def test_check_running(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    data_dir = _setup_orch(tmp_path)
    task_id = _make_assigned_task(data_dir)

    from agentmesh import spawner

    with patch("subprocess.Popen", FakePopen):
        with patch.object(spawner, "create_worktree", return_value=(True, "")):
            record = spawner.spawn(task_id, "agent_spawn", str(repo), data_dir=data_dir)

    # Mock os.kill to simulate running process
    with patch("os.kill"):
        result = spawner.check(record.spawn_id, data_dir=data_dir)
        assert result.running is True
        assert result.exit_code is None


def test_check_exited(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    data_dir = _setup_orch(tmp_path)
    task_id = _make_assigned_task(data_dir)

    from agentmesh import spawner

    with patch("subprocess.Popen", FakePopen):
        with patch.object(spawner, "create_worktree", return_value=(True, "")):
            record = spawner.spawn(task_id, "agent_spawn", str(repo), data_dir=data_dir)

    # Mock os.kill to raise ProcessLookupError (process gone)
    with patch("os.kill", side_effect=ProcessLookupError):
        result = spawner.check(record.spawn_id, data_dir=data_dir)
        assert result.running is False


def test_harvest_success(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    data_dir = _setup_orch(tmp_path)
    task_id = _make_assigned_task(data_dir)

    from agentmesh import spawner

    with patch("subprocess.Popen", FakePopen):
        with patch.object(spawner, "create_worktree", return_value=(True, "")):
            record = spawner.spawn(task_id, "agent_spawn", str(repo), data_dir=data_dir)

    # Write a fake output file
    output_path = Path(record.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({"result": "done", "cost_usd": 0.05}))

    with patch("os.kill", side_effect=ProcessLookupError):
        with patch.object(spawner, "remove_worktree", return_value=(True, "")):
            result = spawner.harvest(record.spawn_id, data_dir=data_dir)

    assert result.outcome == "success"
    assert result.output_data["result"] == "done"

    # Task should be PR_OPEN
    t = db.get_task(task_id, data_dir)
    assert t.state == TaskState.PR_OPEN


def test_harvest_failure(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    data_dir = _setup_orch(tmp_path)
    task_id = _make_assigned_task(data_dir)

    from agentmesh import spawner

    with patch("subprocess.Popen", FakePopen):
        with patch.object(spawner, "create_worktree", return_value=(True, "")):
            record = spawner.spawn(task_id, "agent_spawn", str(repo), data_dir=data_dir)

    # No output file -> failure path
    with patch("os.kill", side_effect=ProcessLookupError):
        with patch.object(spawner, "remove_worktree", return_value=(True, "")):
            result = spawner.harvest(record.spawn_id, data_dir=data_dir)

    assert result.outcome == "failure"

    # Task should be ABORTED
    t = db.get_task(task_id, data_dir)
    assert t.state == TaskState.ABORTED


def test_harvest_raises_if_still_running(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    data_dir = _setup_orch(tmp_path)
    task_id = _make_assigned_task(data_dir)

    from agentmesh import spawner

    with patch("subprocess.Popen", FakePopen):
        with patch.object(spawner, "create_worktree", return_value=(True, "")):
            record = spawner.spawn(task_id, "agent_spawn", str(repo), data_dir=data_dir)

    with patch("os.kill"):  # no error -> still running
        with pytest.raises(spawner.SpawnError, match="still running"):
            spawner.harvest(record.spawn_id, data_dir=data_dir)


def test_abort_spawn(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    data_dir = _setup_orch(tmp_path)
    task_id = _make_assigned_task(data_dir)

    from agentmesh import spawner

    with patch("subprocess.Popen", FakePopen):
        with patch.object(spawner, "create_worktree", return_value=(True, "")):
            record = spawner.spawn(task_id, "agent_spawn", str(repo), data_dir=data_dir)

    with patch("os.kill"):
        with patch.object(spawner, "remove_worktree", return_value=(True, "")):
            updated = spawner.abort(record.spawn_id, reason="timeout", data_dir=data_dir)

    assert updated.outcome == "aborted"

    # Task should be ABORTED
    t = db.get_task(task_id, data_dir)
    assert t.state == TaskState.ABORTED


def test_abort_rejects_already_ended_spawn(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    data_dir = _setup_orch(tmp_path)
    task_id = _make_assigned_task(data_dir)

    from agentmesh import spawner

    with patch("subprocess.Popen", FakePopen):
        with patch.object(spawner, "create_worktree", return_value=(True, "")):
            record = spawner.spawn(task_id, "agent_spawn", str(repo), data_dir=data_dir)

    output_path = Path(record.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({"result": "done"}))

    with patch("os.kill", side_effect=ProcessLookupError):
        with patch.object(spawner, "remove_worktree", return_value=(True, "")):
            spawner.harvest(record.spawn_id, data_dir=data_dir)

    with pytest.raises(spawner.SpawnError, match="already ended"):
        spawner.abort(record.spawn_id, reason="late abort", data_dir=data_dir)

    # Successful harvest outcome must remain intact.
    r = spawner.list_spawns(data_dir=data_dir)[0]
    assert r.outcome == "success"
    t = db.get_task(task_id, data_dir)
    assert t.state == TaskState.PR_OPEN


def test_list_spawns(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    data_dir = _setup_orch(tmp_path)
    task_id = _make_assigned_task(data_dir)

    from agentmesh import spawner

    with patch("subprocess.Popen", FakePopen):
        with patch.object(spawner, "create_worktree", return_value=(True, "")):
            record = spawner.spawn(task_id, "agent_spawn", str(repo), data_dir=data_dir)

    all_spawns = spawner.list_spawns(data_dir=data_dir)
    assert len(all_spawns) == 1
    assert all_spawns[0].spawn_id == record.spawn_id

    active = spawner.list_spawns(active_only=True, data_dir=data_dir)
    assert len(active) == 1


def test_list_spawns_active_filter(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    data_dir = _setup_orch(tmp_path)
    task_id = _make_assigned_task(data_dir)

    from agentmesh import spawner

    with patch("subprocess.Popen", FakePopen):
        with patch.object(spawner, "create_worktree", return_value=(True, "")):
            record = spawner.spawn(task_id, "agent_spawn", str(repo), data_dir=data_dir)

    # Abort it
    with patch("os.kill"):
        with patch.object(spawner, "remove_worktree", return_value=(True, "")):
            spawner.abort(record.spawn_id, reason="done", data_dir=data_dir)

    active = spawner.list_spawns(active_only=True, data_dir=data_dir)
    assert len(active) == 0

    all_spawns = spawner.list_spawns(data_dir=data_dir)
    assert len(all_spawns) == 1


# ---------------------------------------------------------------------------
# WorkerAdapter tests
# ---------------------------------------------------------------------------

from agentmesh.worker_adapters import (
    ClaudeCodeAdapter,
    SpawnSpec,
    WorkerAdapter,
    enforce_adapter_policy,
    get_adapter_load_errors,
    get_adapter,
    load_adapters_from_env,
    load_adapters_from_modules,
    register_adapter,
    _ADAPTERS,
)


def test_adapter_registry() -> None:
    """register_adapter / get_adapter round-trip; unknown name raises."""
    adapter = get_adapter("claude_code")
    assert adapter.name == "claude_code"

    with pytest.raises(ValueError, match="Unknown worker backend"):
        get_adapter("nonexistent_backend_xyz")


def test_load_adapters_from_modules_registers_exported_adapters(tmp_path: Path) -> None:
    mod_name = "am_test_adapter_mod"
    mod_file = tmp_path / f"{mod_name}.py"
    mod_file.write_text(
        "from agentmesh.worker_adapters import SpawnSpec\n"
        "class TempAdapter:\n"
        "    name='temp_mod'\n"
        "    version='v1'\n"
        "    def build_spawn_spec(self, *, context, model, worktree_path, output_dir):\n"
        "        return SpawnSpec(command=['echo', context], output_path=str(output_dir / 'x.json'))\n"
        "    def parse_output(self, output_path):\n"
        "        return (False, {})\n"
        "ADAPTERS=[TempAdapter()]\n",
    )

    sys.path.insert(0, str(tmp_path))
    try:
        new_names = load_adapters_from_modules([mod_name])
        assert "temp_mod" in new_names
        assert get_adapter("temp_mod").name == "temp_mod"
    finally:
        _ADAPTERS.pop("temp_mod", None)
        sys.modules.pop(mod_name, None)
        if sys.path and sys.path[0] == str(tmp_path):
            sys.path.pop(0)


def test_load_adapters_from_modules_records_errors() -> None:
    before = len(get_adapter_load_errors())
    bad_mod = "definitely_missing_agentmesh_adapter_module_xyz"
    new_names = load_adapters_from_modules([bad_mod])
    assert new_names == []
    errs = get_adapter_load_errors()
    assert len(errs) >= before + 1
    assert any(bad_mod in e for e in errs)


def test_load_adapters_from_env_disabled_in_ci(monkeypatch) -> None:
    before = len(get_adapter_load_errors())
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("AGENTMESH_ADAPTER_MODULES", "fake.mod")
    names = load_adapters_from_env()
    assert names == []
    errs = get_adapter_load_errors()
    assert len(errs) >= before + 1
    assert any("disabled in CI" in e for e in errs[-2:])


def test_claude_adapter_build_spec(tmp_path: Path) -> None:
    adapter = ClaudeCodeAdapter()
    wt = tmp_path / "wt"
    wt.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    spec = adapter.build_spawn_spec(
        context="do stuff", model="sonnet",
        worktree_path=wt, output_dir=out_dir,
    )
    assert isinstance(spec, SpawnSpec)
    assert spec.command[0] == "claude"
    assert "--model" in spec.command
    assert "sonnet" in spec.command
    assert "do stuff" in spec.command
    assert spec.output_path == str(out_dir / "claude_output.json")
    assert spec.stdout_to_file is True


def test_claude_adapter_parse_success(tmp_path: Path) -> None:
    from agentmesh.worker_adapters import WorkerOutput
    adapter = ClaudeCodeAdapter()
    out_file = tmp_path / "output.json"
    out_file.write_text(json.dumps({"result": "ok", "cost_usd": 0.01}))

    wo = adapter.parse_output(out_file)
    assert isinstance(wo, WorkerOutput)
    assert wo.success is True
    assert wo.raw["result"] == "ok"
    assert wo.cost_usd == 0.01


def test_claude_adapter_parse_failure(tmp_path: Path) -> None:
    from agentmesh.worker_adapters import WorkerOutput
    adapter = ClaudeCodeAdapter()

    # Missing file
    wo = adapter.parse_output(tmp_path / "nope.json")
    assert wo.success is False
    assert wo.raw == {}

    # Empty file
    empty = tmp_path / "empty.json"
    empty.write_text("")
    wo = adapter.parse_output(empty)
    assert wo.success is False

    # Invalid JSON
    bad = tmp_path / "bad.json"
    bad.write_text("not json {{{")
    wo = adapter.parse_output(bad)
    assert wo.success is False
    assert wo.error_message  # should have a message


def test_claude_adapter_parse_non_numeric_usage_fields(tmp_path: Path) -> None:
    adapter = ClaudeCodeAdapter()
    out_file = tmp_path / "output_bad_usage.json"
    out_file.write_text(
        json.dumps({
            "result": "ok",
            "cost_usd": "not-a-number",
            "num_input_tokens": "NaN",
            "num_output_tokens": None,
        })
    )

    wo = adapter.parse_output(out_file)
    assert wo.success is True
    assert wo.cost_usd == 0.0
    assert wo.tokens_in == 0
    assert wo.tokens_out == 0


def test_spawn_with_explicit_backend(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    data_dir = _setup_orch(tmp_path)
    task_id = _make_assigned_task(data_dir, branch="feat/backend-test")

    from agentmesh import spawner

    with patch("subprocess.Popen", FakePopen):
        with patch.object(spawner, "create_worktree", return_value=(True, "")):
            record = spawner.spawn(
                task_id=task_id,
                agent_id="agent_spawn",
                repo_cwd=str(repo),
                backend="claude_code",
                data_dir=data_dir,
            )

    assert record.backend == "claude_code"


def test_spawn_unknown_backend_raises(tmp_path: Path) -> None:
    data_dir = _setup_orch(tmp_path)
    task_id = _make_assigned_task(data_dir, branch="feat/bad-backend")

    from agentmesh import spawner

    with pytest.raises(spawner.SpawnError, match="Unknown worker backend"):
        spawner.spawn(task_id, "agent_spawn", "/tmp", backend="nonexistent", data_dir=data_dir)


def test_spawn_disallowed_by_adapter_policy(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    policy_dir = repo / ".agentmesh"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "policy.json").write_text(
        json.dumps({"worker_adapters": {"allow_backends": ["some_other_backend"]}})
    )

    data_dir = _setup_orch(tmp_path)
    task_id = _make_assigned_task(data_dir, branch="feat/policy-deny")

    from agentmesh import spawner

    with pytest.raises(spawner.SpawnError, match="disallowed by policy allow_backends"):
        spawner.spawn(
            task_id=task_id,
            agent_id="agent_spawn",
            repo_cwd=str(repo),
            backend="claude_code",
            data_dir=data_dir,
        )


def test_enforce_adapter_policy_disallowed_backend(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    policy_dir = repo / ".agentmesh"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "policy.json").write_text(
        json.dumps({"worker_adapters": {"allow_backends": ["some_other_backend"]}})
    )
    with pytest.raises(ValueError, match="disallowed by policy allow_backends"):
        enforce_adapter_policy("claude_code", repo_cwd=str(repo))


class _EchoAdapter:
    """Minimal custom adapter for testing."""

    name: str = "echo_test"

    def build_spawn_spec(self, *, context, model, worktree_path, output_dir):
        output_path = str(output_dir / "echo_out.json")
        return SpawnSpec(
            command=["echo", context],
            output_path=output_path,
            stdout_to_file=True,
        )

    def parse_output(self, output_path):
        if not output_path.exists():
            return False, {}
        try:
            content = output_path.read_text().strip()
            if content:
                return True, json.loads(content)
        except (json.JSONDecodeError, OSError):
            pass
        return False, {}


def test_custom_adapter_e2e(tmp_path: Path) -> None:
    """Register a custom adapter, spawn with it, harvest output."""
    echo = _EchoAdapter()
    register_adapter(echo)
    try:
        repo = _init_repo(tmp_path)
        data_dir = _setup_orch(tmp_path)
        task_id = _make_assigned_task(data_dir, branch="feat/echo")

        from agentmesh import spawner

        with patch("subprocess.Popen", FakePopen):
            with patch.object(spawner, "create_worktree", return_value=(True, "")):
                record = spawner.spawn(
                    task_id=task_id,
                    agent_id="agent_spawn",
                    repo_cwd=str(repo),
                    backend="echo_test",
                    data_dir=data_dir,
                )

        assert record.backend == "echo_test"

        # Write valid JSON to the adapter's output path so harvest succeeds
        output_path = Path(record.output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps({"echo": True}))

        with patch("os.kill", side_effect=ProcessLookupError):
            with patch.object(spawner, "remove_worktree", return_value=(True, "")):
                result = spawner.harvest(record.spawn_id, data_dir=data_dir)

        assert result.outcome == "success"
        assert result.output_data["echo"] is True
    finally:
        _ADAPTERS.pop("echo_test", None)


def test_backend_persisted_in_db(tmp_path: Path) -> None:
    """Backend column survives DB round-trip."""
    repo = _init_repo(tmp_path)
    data_dir = _setup_orch(tmp_path)
    task_id = _make_assigned_task(data_dir, branch="feat/db-backend")

    from agentmesh import spawner

    with patch("subprocess.Popen", FakePopen):
        with patch.object(spawner, "create_worktree", return_value=(True, "")):
            record = spawner.spawn(
                task_id=task_id,
                agent_id="agent_spawn",
                repo_cwd=str(repo),
                backend="claude_code",
                data_dir=data_dir,
            )

    row = db.get_spawn(record.spawn_id, data_dir)
    assert row is not None
    assert row["backend"] == "claude_code"
    assert "backend_version" in row
    assert row["backend_version"]


def test_worker_output_normalize_from_tuple() -> None:
    """normalize_worker_output handles legacy tuple[bool, dict] format."""
    from agentmesh.worker_adapters import WorkerOutput, normalize_worker_output

    result = normalize_worker_output((True, {"cost_usd": 0.03, "answer": "done"}))
    assert isinstance(result, WorkerOutput)
    assert result.success is True
    assert result.raw["answer"] == "done"
    assert result.cost_usd == 0.03

    fail = normalize_worker_output((False, {}))
    assert fail.success is False
    assert fail.raw == {}


def test_worker_output_normalize_from_malformed_tuple_payload() -> None:
    from agentmesh.worker_adapters import normalize_worker_output

    wo = normalize_worker_output((True, "not-a-dict"))
    assert wo.success is True
    assert wo.raw == {}
    assert wo.cost_usd == 0.0
    assert wo.tokens_in == 0
    assert wo.tokens_out == 0


def test_worker_output_normalize_passthrough() -> None:
    """normalize_worker_output passes WorkerOutput through unchanged."""
    from agentmesh.worker_adapters import WorkerOutput, normalize_worker_output

    wo = WorkerOutput(success=True, raw={"x": 1}, cost_usd=0.5, tokens_in=100)
    assert normalize_worker_output(wo) is wo


def test_harvest_populates_structured_fields(tmp_path: Path) -> None:
    """Harvest result includes cost_usd and token counts from output."""
    repo = _init_repo(tmp_path)
    data_dir = _setup_orch(tmp_path)
    task_id = _make_assigned_task(data_dir)

    from agentmesh import spawner

    with patch("subprocess.Popen", FakePopen):
        with patch.object(spawner, "create_worktree", return_value=(True, "")):
            record = spawner.spawn(task_id, "agent_spawn", str(repo), data_dir=data_dir)

    output_path = Path(record.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({
        "result": "done",
        "cost_usd": 0.07,
        "num_input_tokens": 5000,
        "num_output_tokens": 1200,
    }))

    with patch("os.kill", side_effect=ProcessLookupError):
        with patch.object(spawner, "remove_worktree", return_value=(True, "")):
            result = spawner.harvest(record.spawn_id, data_dir=data_dir)

    assert result.outcome == "success"
    assert result.cost_usd == 0.07
    assert result.tokens_in == 5000
    assert result.tokens_out == 1200


def test_harvest_terminal_task_conflict_fails_closed(tmp_path: Path) -> None:
    """If task became terminal before harvest side effects, harvest should not crash."""
    repo = _init_repo(tmp_path)
    data_dir = _setup_orch(tmp_path)
    task_id = _make_assigned_task(data_dir)

    from agentmesh import spawner

    with patch("subprocess.Popen", FakePopen):
        with patch.object(spawner, "create_worktree", return_value=(True, "")):
            record = spawner.spawn(task_id, "agent_spawn", str(repo), data_dir=data_dir)

    output_path = Path(record.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({"result": "done"}))

    # External controller moved task to terminal before harvest runs.
    orchestrator.abort_task(task_id, reason="external abort", agent_id="agent_spawn", data_dir=data_dir)

    with patch("os.kill", side_effect=ProcessLookupError):
        with patch.object(spawner, "remove_worktree", return_value=(True, "")):
            result = spawner.harvest(record.spawn_id, data_dir=data_dir)

    assert result.outcome == "failure"
    assert result.output_data["error"] == "task_transition_failed"
    row = db.get_spawn(record.spawn_id, data_dir)
    assert row is not None
    assert row["outcome"] == "failure"


def test_harvest_verification_passes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    data_dir = _setup_orch(tmp_path)
    task_id = _make_assigned_task(
        data_dir,
        meta={"verify_tests_command": "pytest -q"},
    )

    from agentmesh import spawner

    with patch("subprocess.Popen", FakePopen):
        with patch.object(spawner, "create_worktree", return_value=(True, "")):
            record = spawner.spawn(task_id, "agent_spawn", str(repo), data_dir=data_dir)

    output_path = Path(record.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({"result": "done"}))

    with patch("os.kill", side_effect=ProcessLookupError):
        with patch.object(spawner, "remove_worktree", return_value=(True, "")):
            with patch.object(spawner, "run_tests", return_value=(True, "all good")):
                result = spawner.harvest(record.spawn_id, data_dir=data_dir)

    assert result.outcome == "success"
    assert result.verification_command == "pytest -q"
    assert result.verification_passed is True
    assert result.verification_summary == "all good"


def test_harvest_verification_failure_emits_test_mismatch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    data_dir = _setup_orch(tmp_path)
    task_id = _make_assigned_task(
        data_dir,
        meta={"verify_tests_command": "pytest -q"},
    )

    from agentmesh import spawner

    with patch("subprocess.Popen", FakePopen):
        with patch.object(spawner, "create_worktree", return_value=(True, "")):
            record = spawner.spawn(task_id, "agent_spawn", str(repo), data_dir=data_dir)

    output_path = Path(record.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({"result": "done"}))

    with patch("os.kill", side_effect=ProcessLookupError):
        with patch.object(spawner, "remove_worktree", return_value=(True, "")):
            with patch.object(spawner, "run_tests", return_value=(False, "assert 1 == 2")):
                result = spawner.harvest(record.spawn_id, data_dir=data_dir)

    assert result.outcome == "failure"
    assert result.output_data["error"] == "test_mismatch"
    assert result.verification_command == "pytest -q"
    assert result.verification_passed is False

    task = db.get_task(task_id, data_dir)
    assert task is not None
    assert task.state == TaskState.ABORTED

    evts = events.read_events(data_dir=data_dir)
    mismatch = [e for e in evts if e.kind == EventKind.TEST_MISMATCH]
    assert len(mismatch) == 1
    assert mismatch[0].payload["spawn_id"] == record.spawn_id


def test_double_harvest_race_fails_closed(tmp_path: Path) -> None:
    """Second concurrent harvest gets SpawnError, not double side effects."""
    repo = _init_repo(tmp_path)
    data_dir = _setup_orch(tmp_path)
    task_id = _make_assigned_task(data_dir)

    from agentmesh import spawner

    with patch("subprocess.Popen", FakePopen):
        with patch.object(spawner, "create_worktree", return_value=(True, "")):
            record = spawner.spawn(task_id, "agent_spawn", str(repo), data_dir=data_dir)

    output_path = Path(record.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({"result": "done"}))

    with patch("os.kill", side_effect=ProcessLookupError):
        with patch.object(spawner, "remove_worktree", return_value=(True, "")):
            result = spawner.harvest(record.spawn_id, data_dir=data_dir)

    assert result.outcome == "success"

    # Second harvest: record.ended_at is now set, caught by pre-CAS check
    with pytest.raises(spawner.SpawnError, match="already harvested"):
        spawner.harvest(record.spawn_id, data_dir=data_dir)


def test_double_abort_race_fails_closed(tmp_path: Path) -> None:
    """Second concurrent abort gets SpawnError, not double side effects."""
    repo = _init_repo(tmp_path)
    data_dir = _setup_orch(tmp_path)
    task_id = _make_assigned_task(data_dir)

    from agentmesh import spawner

    with patch("subprocess.Popen", FakePopen):
        with patch.object(spawner, "create_worktree", return_value=(True, "")):
            record = spawner.spawn(task_id, "agent_spawn", str(repo), data_dir=data_dir)

    with patch("os.kill"):
        with patch.object(spawner, "remove_worktree", return_value=(True, "")):
            spawner.abort(record.spawn_id, reason="first", data_dir=data_dir)

    # Second abort: ended_at is set, caught by pre-CAS check
    with pytest.raises(spawner.SpawnError, match="already ended"):
        spawner.abort(record.spawn_id, reason="second", data_dir=data_dir)


def test_harvest_unknown_backend_fails_closed(tmp_path: Path) -> None:
    """Missing adapter at harvest-time should not crash; it should fail closed."""
    repo = _init_repo(tmp_path)
    data_dir = _setup_orch(tmp_path)
    task_id = _make_assigned_task(data_dir, branch="feat/unknown-backend")
    orchestrator.transition_task(task_id, TaskState.RUNNING, agent_id="agent_spawn", data_dir=data_dir)

    from datetime import datetime, timezone
    from agentmesh import spawner

    spawn_id = "spawn_unknown_backend"
    output_path = repo / ".worktrees" / "unknown" / ".agentmesh" / "out.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({"result": "ok"}))

    db.create_spawn(
        spawn_id=spawn_id,
        task_id=task_id,
        attempt_id="",
        agent_id="agent_spawn",
        pid=99999,
        worktree_path=str(repo / ".worktrees" / "unknown"),
        branch="feat/unknown-backend",
        episode_id="",
        context_hash="sha256:abc",
        started_at=datetime.now(timezone.utc).isoformat(),
        output_path=str(output_path),
        repo_cwd=str(repo),
        backend="missing_backend",
        data_dir=data_dir,
    )

    with patch("os.kill", side_effect=ProcessLookupError):
        with patch.object(spawner, "remove_worktree", return_value=(True, "")):
            result = spawner.harvest(spawn_id, data_dir=data_dir)

    assert result.outcome == "failure"
    assert result.output_data["error"] == "unknown_backend"
    t = db.get_task(task_id, data_dir)
    assert t.state == TaskState.ABORTED


# ---------------------------------------------------------------------------
# Worker runtime env sanitization (build_child_env)
# ---------------------------------------------------------------------------

def test_build_child_env_strips_claudecode() -> None:
    """build_child_env strips CLAUDECODE from subprocess env by default."""
    from agentmesh.spawner import build_child_env

    sentinel = "test_nested_guard"
    with patch.dict(os.environ, {"CLAUDECODE": sentinel, "PATH": "/usr/bin"}):
        env, stripped = build_child_env()

    assert "CLAUDECODE" not in env
    assert "CLAUDECODE" in stripped
    assert env["PATH"] == "/usr/bin"


def test_build_child_env_does_not_mutate_parent() -> None:
    """build_child_env must copy os.environ, never mutate it."""
    from agentmesh.spawner import build_child_env

    with patch.dict(os.environ, {"CLAUDECODE": "1"}):
        env_before = dict(os.environ)
        build_child_env()
        env_after = dict(os.environ)

    assert env_before == env_after, "build_child_env mutated os.environ"


def test_build_child_env_policy_strip() -> None:
    """Policy worker_runtime.strip_env extends the default deny set."""
    from agentmesh.spawner import build_child_env

    policy = {"worker_runtime": {"strip_env": ["MY_SECRET_VAR"]}}
    with patch.dict(os.environ, {"CLAUDECODE": "1", "MY_SECRET_VAR": "s3cret", "KEEP": "yes"}):
        env, stripped = build_child_env(policy=policy)

    assert "CLAUDECODE" not in env
    assert "MY_SECRET_VAR" not in env
    assert "KEEP" in env
    assert sorted(stripped) == ["CLAUDECODE", "MY_SECRET_VAR"]


def test_build_child_env_spec_env_wins() -> None:
    """Adapter spec.env overrides after sanitization (deliberate re-inject)."""
    from agentmesh.spawner import build_child_env

    with patch.dict(os.environ, {"CLAUDECODE": "1"}):
        env, stripped = build_child_env(spec_env={"CLAUDECODE": "override"})

    assert env["CLAUDECODE"] == "override"
    assert "CLAUDECODE" in stripped


def test_spawn_emits_env_sanitized_event(tmp_path: Path) -> None:
    """WORKER_SPAWN event includes env_sanitized and stripped_keys fields."""
    repo = _init_repo(tmp_path)
    data_dir = _setup_orch(tmp_path)
    task_id = _make_assigned_task(data_dir, branch="feat/env-sanitize")

    from agentmesh import spawner

    with patch.dict(os.environ, {"CLAUDECODE": "nested"}):
        with patch("subprocess.Popen", FakePopen):
            with patch.object(spawner, "create_worktree", return_value=(True, "")):
                spawner.spawn(
                    task_id=task_id,
                    agent_id="agent_spawn",
                    repo_cwd=str(repo),
                    data_dir=data_dir,
                )

    all_events = events.read_events(data_dir=data_dir)
    spawn_events = [e for e in all_events if e.kind == EventKind.WORKER_SPAWN]
    assert len(spawn_events) >= 1
    payload = spawn_events[-1].payload

    assert payload["env_sanitized"] is True
    assert "CLAUDECODE" in payload["stripped_keys"]
