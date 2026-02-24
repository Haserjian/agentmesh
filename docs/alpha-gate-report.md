# Alpha Gate Report (Sanitized Implementation Summary)

This document is safe for public OSS documentation.

- It summarizes capabilities and checks.
- It does **not** embed sensitive canary internals.
- Raw run artifacts should remain private (see [`public-private-boundary.md`](./public-private-boundary.md)).

## Implemented Items

1. **Orchestrator lease lock**
   - Added orchestration lease control via typed lock claim (`LOCK:orchestration`).
   - Mutating control-plane commands now acquire/release lease with fail-fast conflict handling.
   - `--json` lock conflicts emit structured error payload.
   - `orch abort-all` force-clears orchestration lease locks.

2. **Adapter trust boundary hardening**
   - Added adapter allowlist policy gate (`worker_adapters.allow_backends|allow_modules|allow_paths`).
   - Disabled env-based adapter autoload when `CI=true`.
   - Added structured adapter metadata logging (`ADAPTER_LOAD` event with backend/module/origin/version).
   - Disallowed adapters fail closed in spawn path.

3. **Cost budget enforcement**
   - Added task-level `--max-cost-usd` (stored in task meta).
   - Watchdog enforces cumulative cost from `WORKER_DONE` events.
   - Over-budget tasks emit weave receipt + `COST_EXCEEDED` event and are aborted.

4. **Emergency controls**
   - Added `agentmesh orch freeze` / `--off`.
   - Added `agentmesh orch lock-merges` / `--off`.
   - Added `agentmesh orch abort-all --reason ...`.
   - Enforcement:
     - Spawns blocked while frozen.
     - `REVIEW_PASS -> MERGED` blocked while merges are locked.

5. **Observability**
   - Added `agentmesh orch watch` with polling stream.
   - `--json` mode outputs JSON lines for task/spawn/watchdog/merge-control events.

6. **Alpha gate harness**
   - Added machine-report module: `src/agentmesh/alpha_gate.py`
   - Added CLI script: `scripts/alpha_gate_report.py`
   - Added operator runbook: `docs/alpha-gate-runbook.md`
   - Gate report utility for machine output (store raw output in private artifact path)

## Gate Check Results

Source: deterministic local harness run (sanitized summary)

| Check | Result |
|---|---|
| merged task count >= 1 | PASS |
| witness VERIFIED in CI path | PASS |
| full transition receipts present | PASS |
| watchdog handled >= 1 event | PASS |
| no orphan/finalization loss | PASS |

## Validation Commands

```bash
cd /path/to/agentmesh
uv run pytest -q tests/test_orch_cli.py tests/test_spawner.py tests/test_watchdog.py tests/test_orchestrator.py tests/test_alpha_gate.py tests/test_worker_cli.py tests/test_watchdog_cli.py
uv run pytest -q
uv run python - <<'PY'
from pathlib import Path
from agentmesh import db, events, orchestrator
from agentmesh.models import Agent, TaskState, EventKind, _now
from agentmesh.alpha_gate import write_alpha_gate_report
data_dir = Path('tmp_alpha_gate_data')
data_dir.mkdir(exist_ok=True)
db.init_db(data_dir)
db.register_agent(Agent(agent_id='alpha_agent', cwd='/tmp'), data_dir)
t = orchestrator.create_task('alpha demo', data_dir=data_dir)
orchestrator.assign_task(t.task_id, 'alpha_agent', branch='feat/alpha-demo', data_dir=data_dir)
orchestrator.transition_task(t.task_id, TaskState.RUNNING, data_dir=data_dir)
orchestrator.transition_task(t.task_id, TaskState.PR_OPEN, data_dir=data_dir)
orchestrator.transition_task(t.task_id, TaskState.CI_PASS, data_dir=data_dir)
orchestrator.transition_task(t.task_id, TaskState.REVIEW_PASS, data_dir=data_dir)
orchestrator.complete_task(t.task_id, agent_id='alpha_agent', data_dir=data_dir)
db.create_spawn('spawn_alpha', t.task_id, 'att_alpha', 'alpha_agent', 123, '/tmp/wt', 'feat/alpha-demo', '', 'sha256:abc', _now(), data_dir=data_dir)
db.update_spawn('spawn_alpha', ended_at=_now(), outcome='success', data_dir=data_dir)
events.append_event(EventKind.GC, payload={'watchdog':'scan','harvested_spawns':['spawn_alpha']}, data_dir=data_dir)
write_alpha_gate_report(Path('.agentmesh/runs/alpha-gate-report.json'), data_dir, ci_log_text='VERIFIED', require_witness_verified=True)
PY
```

## Residual Risks

1. **Lease is CLI-process scoped**: this prevents concurrent mutating controllers, but long-lived external orchestrator daemons should keep renewing lease by design (future enhancement).
2. **Cost enforcement is event-driven**: budget checks depend on emitted `WORKER_DONE` metrics and can only enforce on known cumulative spend.
3. **Witness gate in report depends on CI log input**: report correctness for witness status requires passing real CI output.
