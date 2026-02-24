# AgentMesh Repo Playbook

This repo uses AgentMesh as a local coordination + provenance layer around normal git workflows.

## Core Rules

1. Use `agentmesh task start` at the beginning of work.
2. Use `agentmesh task finish` for commits that should carry provenance.
3. If `task` wrappers are not used, at minimum use `agentmesh commit` instead of plain `git commit`.
4. In multi-agent runs, claim resources before editing.

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
