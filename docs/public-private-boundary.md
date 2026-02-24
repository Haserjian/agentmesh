# Public vs Private Boundary

This project uses an intentional split:

- **Public OSS (AgentMesh repo)** drives adoption, trust, and install growth.
- **Private operations + strategy (CCIO/internal)** preserve commercial leverage.

## Public (safe to ship in OSS)

- Core runtime capabilities:
  - orchestration lease lock/renew
  - freeze / lock-merges / abort-all controls
  - spawner/watchdog hardening
  - adapter allowlist policy gates
  - task cost budgets
  - independent test-isolation verification
  - dependency DAG + cycle checks
- Tests for the above behavior.
- Generic docs/runbooks without sensitive repo or customer context.
- CI integration docs and `agentmesh-action` usage.

## Private (keep internal)

- GTM, pricing, positioning, and competitive strategy.
- Internal planning docs and sequencing rationale.
- Real canary artifacts with sensitive details:
  - repository names
  - branch names
  - CI internals/logs
  - cost/latency breakdowns
  - operational identifiers
- Enterprise roadmap details not ready for public commitments.

## Repo Hygiene Rules

1. Commit code + tests.
2. Do not commit raw run artifacts by default.
3. Publish sanitized summaries/templates only.
4. Keep full evidence bundles in private storage.

## Executable Guardrail

Use the classifier before publishing:

```bash
agentmesh classify --staged --fail-on-private --fail-on-review
```

- Exit `2`: private files present.
- Exit `3`: review-required files present.

Use full release preflight for automation:

```bash
agentmesh release-check --staged --require-witness --json
```

In CI, you can roll this out in two phases:

- **Phase 1 (preview/non-blocking):** collect release-check artifacts.
- **Phase 2 (blocking):** fail PRs on release-check non-zero exit.

## Recommended Workflow

1. Run canary and store full artifacts privately.
2. Generate a sanitized public summary from those artifacts.
3. Publish public notes describing capabilities and outcomes, not sensitive internals.

## Why This Split Exists

- **Public layer** proves technical capability and accelerates adoption.
- **Private layer** retains monetizable operational intelligence and enterprise differentiation.

## Monetization Alignment

- OSS remains the distribution channel (install growth, trust, ecosystem integrations).
- Paid value should concentrate on:
  - managed evidence operations and reporting
  - enterprise policy packs and compliance controls
  - historical analytics/replay and SLA-backed insights
  - private implementation playbooks and support workflows
