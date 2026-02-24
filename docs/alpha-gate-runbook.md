# Alpha Gate Runbook

This runbook executes a single-task orchestrated run and emits a machine-readable gate report.

## Artifact Handling Policy

- Treat raw canary artifacts as **private** by default.
- Store raw outputs outside public docs (example path used below: `.agentmesh/runs/`).
- Publish only sanitized summaries in OSS docs.

## Prereqs

- Local repo initialized with `agentmesh init`
- Hooks installed if using Claude Code (`agentmesh hooks install`)
- Witness deps installed if strict witness checks are required:

```bash
pip install "agentmesh-core[witness]"
```

## 1) Start with a clean control plane

```bash
agentmesh orch abort-all --reason "alpha gate reset"
agentmesh orch freeze --off
agentmesh orch lock-merges --off
```

If you're running a long-lived external orchestrator loop, renew lease periodically:

```bash
agentmesh orch lease-renew --owner "$AGENTMESH_ORCH_OWNER" --ttl 300 --json
```

## 2) Create and assign one task

```bash
TASK_ID=$(agentmesh --data-dir "$AGENTMESH_DATA_DIR" orch create \
  --title "Alpha gate canary task" \
  --description "One real orchestrated PR run" \
  --max-cost-usd 2.50 \
  --json | jq -r .task_id)

agentmesh --data-dir "$AGENTMESH_DATA_DIR" orch assign "$TASK_ID" --branch feat/alpha-gate --json
```

## 3) Spawn a worker

```bash
SPAWN_ID=$(agentmesh --data-dir "$AGENTMESH_DATA_DIR" worker spawn "$TASK_ID" \
  --backend claude_code --json | jq -r .spawn_id)
```

## 4) Monitor and harvest

```bash
agentmesh --data-dir "$AGENTMESH_DATA_DIR" orch watch --json
agentmesh --data-dir "$AGENTMESH_DATA_DIR" watchdog --json
agentmesh --data-dir "$AGENTMESH_DATA_DIR" worker harvest "$SPAWN_ID" --json
```

## 5) Advance to merged (if policy gates are satisfied)

```bash
agentmesh --data-dir "$AGENTMESH_DATA_DIR" orch advance "$TASK_ID" --to ci_pass --json
agentmesh --data-dir "$AGENTMESH_DATA_DIR" orch advance "$TASK_ID" --to review_pass --json
agentmesh --data-dir "$AGENTMESH_DATA_DIR" orch advance "$TASK_ID" --to merged --json
```

## 6) Generate Alpha Gate report

```bash
python scripts/alpha_gate_report.py \
  --data-dir "$AGENTMESH_DATA_DIR" \
  --ci-result-json ./.agentmesh/runs/ci-result.json \
  --ci-log ./.agentmesh/runs/ci-witness.log \
  --out ./.agentmesh/runs/alpha-gate-report.json
```

If CI log is not available locally:

```bash
python scripts/alpha_gate_report.py \
  --data-dir "$AGENTMESH_DATA_DIR" \
  --witness-optional \
  --out ./.agentmesh/runs/alpha-gate-report.json
```

## Required checks in report

- `merged_task_count.pass == true`
- `witness_verified_ci.pass == true` (unless witness optional mode was used)
- `full_transition_receipts.pass == true`
- `watchdog_handled_event.pass == true`
- `no_orphan_finalization_loss.pass == true`

## Public Output Pattern

- Keep full JSON report private.
- Publish sanitized summary in markdown (without sensitive repo/CI details).
- If you need a public JSON example, use [`docs/alpha-gate-report.template.json`](./alpha-gate-report.template.json) rather than a real run artifact.
