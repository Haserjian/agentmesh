# Threadwork

> Threadwork is the provenance engine in the [Assay](https://github.com/Haserjian/assay) ecosystem.

[![PyPI](https://img.shields.io/pypi/v/agentmesh-core)](https://pypi.org/project/agentmesh-core/)
[![Tests](https://img.shields.io/badge/tests-318%20passed-brightgreen)]()
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

Local-first multi-agent coordination and provenance for coding workflows.

Threadwork adds deterministic coordination (claims, waits, steals), commit-linked lineage (`AgentMesh-Episode` trailers + weave events), and portable handoff bundles (`.meshpack`) on top of normal git workflows. It can be adopted standalone.

Compatibility note: the repo slug, package, CLI, commit trailers, and state
paths still use `agentmesh` naming during the migration window. `Threadwork` is
the canonical public name; `agentmesh` remains the runtime compatibility alias
for now.

Naming invariant during migration: `Threadwork` is the public name for the
provenance and coordination substrate. The implementation may continue to use
`agentmesh` for repository, package, CLI, config, schema, state-path, and
trailer compatibility until an explicit migration plan exists. Do not rename
compatibility surfaces as part of narrative/docs cleanup alone.

For the ecosystem lineage note, see the shared workspace doc at `/Users/timmybhaserjian/docs/specs/assay-ecosystem-map.md`.

## Authority Layers

- Canonical policy core: [`trust/*`](./trust/), [`AGENTS.md`](./AGENTS.md), [`docs/public-private-boundary.md`](./docs/public-private-boundary.md), [`README.md`](./README.md), [`docs/license-policy.md`](./docs/license-policy.md)
- Operational guidance: [`docs/alpha-gate-runbook.md`](./docs/alpha-gate-runbook.md), [`docs/alpha-gate-report.md`](./docs/alpha-gate-report.md), [`docs/pilot/*`](./docs/pilot/)
- Frontier / spec material: [`docs/evidence-wire-protocol-v0.md`](./docs/evidence-wire-protocol-v0.md), [`docs/spec-meshpack-signing-v0.4.md`](./docs/spec-meshpack-signing-v0.4.md), [`docs/spec/cwe-v1.md`](./docs/spec/cwe-v1.md)

When these layers differ, the canonical policy core controls current truth.

## Install

```bash
pipx install agentmesh-core   # recommended (isolated)
# or
pip install agentmesh-core    # on Windows: py -m pip install agentmesh-core

# optional: commit witness signing (Ed25519)
pipx install "agentmesh-core[witness]"
```

## Quick Start

CLI examples below use `agentmesh`, which remains the compatibility command
during migration.

```bash
cd your-repo
agentmesh init --install-hooks    # set up mesh + Claude Code hooks
agentmesh doctor                  # verify everything is wired correctly

agentmesh task start --title "Fix login timeout" \
  --claim src/auth.py --claim tests/test_auth.py

# edit + stage as normal
git add src/auth.py tests/test_auth.py

agentmesh task finish --message "Fix login timeout handling" \
  --run-tests "pytest -q tests/test_auth.py"
# ^ commits with AgentMesh-Episode trailer, emits capsule, releases claims
```

## How It Works

When multiple AI agents (or humans) work in the same repo, Threadwork prevents chaos:

- **Claims**: agents lock files/ports/resources before editing. Conflicts are blocked, not merged.
- **Episodes**: every work session gets a unique ID (`ep_...`) that binds claims, capsules, and commits.
- **Capsules**: structured context bundles (SBAR format) for zero-ramp-up handoffs between agents.
- **Weaver**: hash-chained provenance linking capsules to git commits. Every change is traceable. Gap detection catches omissions (`WEAVE_CHAIN_BREAK` event on failure).
- **Witness**: optional Ed25519 signing of commits and provenance records. Adds cryptographic proof of authorship.
- **Commit trailers**: `agentmesh commit` injects `AgentMesh-Episode:` by default, and can attach signed witness trailers when witness support + keys are present.

## Evidence Pipeline

Threadwork integrates with [Assay](https://github.com/Haserjian/assay) to
produce tamper-evident evidence automatically:

- **Role split**: Threadwork records runtime lineage and provenance; Assay verifies and packages trust artifacts. Threadwork answers "how did this work happen?" Assay answers "what can we prove about it?"

- **Assay Bridge**: every merged or aborted task emits an `ASSAY_RECEIPT` event via subprocess call. If Assay isn't installed, the bridge degrades gracefully.
- **Alpha Gate**: release gating with 6 checks (merged task count, witness verification, weave chain integrity, full transition receipts, watchdog handling, no orphan loss).
- **Evidence KPI**: nightly workflow tracking evidence pipeline health — pass rates, enforcement dates, trend history.
- **Evidence Wire Protocol v0**: canonical `_ewp_*` identity envelope for cross-repo evidence flow.
- **OpenClaw integration (via Assay)**: Assay ships a bounded OpenClaw integration — a subprocess-membrane adapter with receipt emission, allowlist enforcement, and signed proof-pack verification. Try it with `assay try-openclaw`. See the [OpenClaw support doc](https://github.com/Haserjian/assay/blob/main/docs/openclaw-support.md).

In the full Assay-integrated reference workflow, configure branch protection so PRs to `main` require lineage + assay-gate + assay-verify + weave-integrity checks. `assay-gate` is the baseline evidence-readiness score; `assay-verify` is the cryptographic proof-pack verification step.

## Optional Witness Signing

If installed with `agentmesh-core[witness]`, you can sign commit witnesses locally:

```bash
agentmesh key generate
agentmesh commit -m "Fix timeout handling"
agentmesh witness verify HEAD
```

Witness verification is portable: the signed witness payload is embedded in commit trailers, so CI and other machines can verify without local sidecar state.

## CI Integration

Add [`agentmesh-action`](https://github.com/Haserjian/agentmesh-action) to check lineage coverage on PRs:

```yaml
# .github/workflows/lineage.yml
name: Lineage Check
on: [pull_request]

permissions:
  contents: read
  pull-requests: write

jobs:
  lineage:
    runs-on: ubuntu-latest
    steps:
      - uses: Haserjian/agentmesh-action@v2
```

The action posts a sticky PR comment showing commit coverage. Set `require-trailers: "true"` to enforce episode lineage, and `verify-witness: "true"` + `require-witness: "true"` to enforce cryptographic witness verification. See [agentmesh-action](https://github.com/Haserjian/agentmesh-action) for policy profiles (`baseline`, `strict`, `enterprise`).

## Documentation

- Coordination playbook: [`AGENTS.md`](./AGENTS.md)
- Public/private boundary: [`docs/public-private-boundary.md`](./docs/public-private-boundary.md)
- Alpha gate runbook: [`docs/alpha-gate-runbook.md`](./docs/alpha-gate-runbook.md)
- Evidence Wire Protocol: [`docs/evidence-wire-protocol-v0.md`](./docs/evidence-wire-protocol-v0.md)
- License transition: [`docs/license-policy.md`](./docs/license-policy.md)

## Related Repos

| Repo | Purpose |
|------|---------|
| [assay](https://github.com/Haserjian/assay) | Evidence compiler CLI (tamper-evident audit trails for AI) |
| [assay-verify-action](https://github.com/Haserjian/assay-verify-action) | GitHub Action for CI evidence verification |
| [assay-ledger](https://github.com/Haserjian/assay-ledger) | Public transparency ledger |
| [assay-protocol](https://github.com/Haserjian/assay-protocol) | Normative protocol and conformance/spec layer |
| [agentmesh-action](https://github.com/Haserjian/agentmesh-action) | GitHub Action for lineage + witness checks |

## License

Threadwork is licensed under Apache-2.0 for current and future development.

Published releases up to and including `v0.7.0` remain under MIT as originally released.
