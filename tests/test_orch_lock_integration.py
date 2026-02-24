"""Integration tests for orchestration lease lock across processes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from typer.testing import CliRunner

from agentmesh import db
from agentmesh.cli import app

runner = CliRunner()


def _invoke(args: list[str], tmp_path: Path):
    return runner.invoke(app, ["--data-dir", str(tmp_path)] + args)


def test_orch_lock_conflict_across_processes(tmp_path: Path) -> None:
    db.init_db(tmp_path)

    root = Path(__file__).resolve().parents[1]
    src = root / "src"
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(src) + (os.pathsep + existing if existing else "")

    holder_code = r"""
import sys
import time
from pathlib import Path
from agentmesh import db, orch_control
dd = Path(sys.argv[1])
db.init_db(dd)
owner = orch_control.make_owner('holder')
ok, _, conflicts = orch_control.acquire_lease(owner=owner, data_dir=dd, ttl_s=20)
if not ok:
    print('LOCK_FAIL', conflicts, flush=True)
    raise SystemExit(2)
print('READY', flush=True)
time.sleep(2.0)
orch_control.release_lease(owner, data_dir=dd)
"""

    proc = subprocess.Popen(
        [sys.executable, "-c", holder_code, str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        # Wait for holder process to confirm lock acquisition.
        ready = False
        deadline = time.time() + 5.0
        while time.time() < deadline:
            line = proc.stdout.readline() if proc.stdout else ""
            if not line:
                time.sleep(0.05)
                continue
            if "READY" in line:
                ready = True
                break
        assert ready, "holder process did not acquire lock"

        blocked = _invoke(["orch", "create", "--title", "blocked", "--json"], tmp_path)
        assert blocked.exit_code == 1
        payload = json.loads(blocked.output)
        assert payload["error"] == "orchestration_lock_conflict"
    finally:
        proc.wait(timeout=10)

    # After lease holder exits and releases lock, create should succeed.
    ok = _invoke(["orch", "create", "--title", "after_release", "--json"], tmp_path)
    assert ok.exit_code == 0
    payload = json.loads(ok.output)
    assert payload["task_id"].startswith("task_")
