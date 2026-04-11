# AgentMesh Repo Playbook

This repo uses AgentMesh as a local coordination + provenance layer around normal git workflows.

## Core Rules

1. Use `agentmesh task start` at the beginning of work.
2. Use `agentmesh task finish` for commits that should carry provenance.
3. If `task` wrappers are not used, at minimum use `agentmesh commit` instead of plain `git commit`.
4. In multi-agent runs, claim resources before editing.
5. Before publishing docs/artifacts, run `agentmesh classify --staged --json` and resolve all `private`/`review` results.
6. Before release/PR finalization, run `agentmesh release-check --staged --json`.

## Codex Skill Chain

Use the smallest applicable chain for the task.

- Unfamiliar repo work: `workspace-scout` -> `repo-archaeology` -> `ownership-map`
- Planned code changes: `patch-planner` -> `change-surgeon`
- Feature requests: `feature-to-impl` -> `patch-planner`
- Structural cleanup: `refactor-runner`
- Large patch review / handoff: `diff-compressor`
- Thread continuation: `handoff-compiler`
- Adversarial review: `diff-compressor` -> `reviewer-simulator`
- Failures and regressions: `test-triage` -> `debug-loop`; flaky or config/default regressions: `repro-pack` -> `test-triage` -> `debug-loop`; CI-owned failures after triage: `ci-fixer`
- Browser-visible failures: `repro-pack` -> `browser-debug` -> `debug-loop`
- Incident / parity break analysis: `incident-forensics`
- Reproduction path: `incident-forensics` -> `repro-pack` -> `debug-loop`
- Merge or branch conflict cleanup: `merge-reconciler`
- Public API, schema, or CLI drift: `api-contract`
- Documentation drift: `docs-sync`
- Config and env blast radius: `config-surgeon`
- Rollouts and staged transitions: `migration-planner`
- Release gate / ship decision: `api-contract` -> `docs-sync` -> `config-surgeon` -> `migration-planner` -> `release-guard`
- Skill quality and routing checks: `skill-evals`
- Skill routing checks: `skill-evals` -> `trigger-auditor` -> `routing-lab`
- Benchmark and scenario curation: `skill-evals` -> `benchmark-curator`
- Skill versioning and promotion: `skill-registry`
- Chain selection and routing plans: `chain-orchestrator`
- Trajectory and continuation traces: `handoff-compiler` -> `trajectory-ledger`
- Tool / MCP contract stress testing: `tool-harness`
- Exploratory ideation: `idea-forge`
- Tool / MCP contract shaping: `tool-contract-optimizer`
- Identity / auth / permission probing: `boundary-fuzzer`
- Long-horizon eval design: `scenario-weaver`
- Risky decisions and preflight: `constitutional-think`

## Happy Path (Humans + Agents)

```bash
# begin or resume tracked work
agentmesh task start --title "<task title>" \
  --claim src/path1.py --claim tests/test_path1.py

# edit + stage normally
git add src/path1.py tests/test_path1.py

# commit with provenance, release claims, and close episode
agentmesh task finish --message "<commit message>" \
  --run-tests "pytest -q"
```

## Manual Flow (Advanced)

```bash
agentmesh episode start --title "<task title>"
agentmesh claim <resource...> --reason "<why>"
git add <files...>
agentmesh commit -m "<commit message>" --run-tests "pytest -q" --capsule
agentmesh weave export --md
agentmesh episode end
```

## Multi-Agent Coordination

- Use `agentmesh claim` for files and shared resources (`PORT:3000`, `LOCK:npm`, `TEST_SUITE:integration`).
- Use `agentmesh check <path>` before edits if unsure who owns it.
- Use `agentmesh wait <resource>` and `agentmesh steal <resource>` for contention.
- Use `agentmesh status` for a live mesh snapshot.

## Handoff and Audit

- `agentmesh bundle emit --task "<handoff note>"` creates a context capsule.
- `agentmesh weave trace <path>` shows provenance events touching a file.
- `agentmesh weave verify` checks chain integrity.
- `agentmesh episode export <episode_id>` creates a portable `.meshpack`.

## Public vs Private Guardrail

- `agentmesh classify --staged` classifies staged files as `public`, `private`, or `review`.
- `agentmesh release-check --staged --json` runs classifier + weave verification (+ optional witness/tests) with strict exit codes.
- `agentmesh sanitize-alpha-gate-report --in <private_json> --out <public_json>` converts private gate outputs to publishable artifacts.
- `private` means keep out of OSS unless fully sanitized.
- `review` means manual decision required before publishing.
- Use `agentmesh classify --staged --fail-on-private --fail-on-review` in release prep.
