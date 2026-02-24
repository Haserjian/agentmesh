# AgentMesh Repo Playbook

This repo uses AgentMesh as a local coordination + provenance layer around normal git workflows.

## Core Rules

1. Use `agentmesh task start` at the beginning of work.
2. Use `agentmesh task finish` for commits that should carry provenance.
3. If `task` wrappers are not used, at minimum use `agentmesh commit` instead of plain `git commit`.
4. In multi-agent runs, claim resources before editing.
5. Before publishing docs/artifacts, run `agentmesh classify --staged --json` and resolve all `private`/`review` results.
6. Before release/PR finalization, run `agentmesh release-check --staged --json`.

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
