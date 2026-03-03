# AgentMesh Project Guide

Local-first multi-agent coordination and provenance system for coding workflows. Adds deterministic coordination, commit-linked lineage, and portable handoff bundles on top of git.

## Key paths

| Area | Path | Purpose |
|------|------|---------|
| CLI | `src/agentmesh/cli.py` | Main entry point (`agentmesh` command) |
| Database | `src/agentmesh/db.py` | Episode/claim/state persistence |
| Orchestrator | `src/agentmesh/orchestrator.py` | Multi-agent task coordination |
| Spawner | `src/agentmesh/spawner.py` | Agent spawn and lifecycle |
| Weaver | `src/agentmesh/weaver.py` | Hash-chained provenance |
| Witness | `src/agentmesh/witness.py` | Ed25519 cryptographic signing |
| Claims | `src/agentmesh/claims.py` | File/resource locking |
| Capsules | `src/agentmesh/capsules.py` | SBAR context bundles for handoffs |
| Public/private | `src/agentmesh/public_private.py` | Classification layer |
| Assay bridge | `src/agentmesh/assay_bridge.py` | Receipt standard integration |
| Alpha gate | `src/agentmesh/alpha_gate.py` | Release gating |
| Evidence KPI | `src/agentmesh/evidence_kpi.py` | KPI tracking |
| Playbook | `AGENTS.md` | Coordination playbook |

## Core concepts

- **Episodes**: Unique work session IDs binding claims, capsules, and commits
- **Claims**: File/port locks acquired before editing (prevents conflicts)
- **Capsules**: SBAR context bundles for agent-to-agent handoffs
- **Weaver**: Hash-chained provenance linking every change to its history
- **Witness**: Optional Ed25519 signing of commits and provenance
- **Commit trailers**: `AgentMesh-Episode: <id>` + optional witness signatures

## Commands

```bash
# Core workflow
agentmesh episode start --title "my task"   # Start an episode
agentmesh claim <file>                      # Lock a file
agentmesh bundle emit                       # Create handoff capsule
agentmesh episode end                       # Close episode

# Happy-path wrapper (start + claim + finish in fewer steps)
agentmesh task start --title "my task" --claim src/foo.py
agentmesh task finish --message "feat: done"

# Classification
agentmesh classify --staged --fail-on-private
agentmesh release-check --staged --json

# Tests
python3 -m pytest tests/ -q
```

## Conventions

- **Commits**: conventional format `type(scope): description`
- **Public/private boundary**: `agentmesh classify` gates CI — private artifacts must not leak
- **Auth**: gh CLI keyring (do NOT set GITHUB_TOKEN env var)

## CI workflows

| Workflow | What it does |
|----------|--------------|
| `ci.yml` | Public-private guard + release-check preview |
| `assay-pilot.yml` | Assay evidence gate |
| `branch-protection-drift.yml` | Detect protection rule drift |
| `evidence-enforcement-monitor.yml` | Monitor evidence enforcement |
| `evidence-kpi.yml` | Track evidence KPIs |
| `dco.yml` | Developer Certificate of Origin |
| `lineage.yml` | Provenance lineage check |
| `publish.yml` | PyPI publication gate |
