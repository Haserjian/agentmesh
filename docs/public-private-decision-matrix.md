# Public vs Private Decision Matrix

Use this matrix when deciding whether content belongs in the OSS repo.

## Step 1: Classify Automatically

Run:

```bash
agentmesh classify --staged --json
```

- `private`: keep internal unless fully sanitized.
- `review`: manual decision required.
- `public`: generally safe to publish.

## Step 2: Apply Commercial Lens

If uncertain, use this rule:

- Publish what proves **capability**.
- Keep private what encodes **advantage**.

### Publish (Capability Signal)

- Product behavior, APIs, tests, reliability fixes.
- Generic architecture and operator runbooks.
- Open benchmarks and sanitized implementation summaries.

### Keep Private (Advantage Signal)

- GTM/pricing/packaging strategy.
- Customer-specific controls and internal roadmap sequencing.
- Raw canary evidence (internal repos, CI internals, cost/latency traces).
- Internal playbooks that materially improve win-rate or margins.

## Step 3: Redaction Checklist (for any review/private artifact)

1. Remove internal repo names and branch identifiers.
2. Remove CI internals and tokenized URLs.
3. Remove raw cost/latency traces unless intentionally public.
4. Replace absolute local paths with generic placeholders.
5. Re-run `agentmesh classify` after edits.

## Release Gate (recommended)

Before merge/publish:

```bash
agentmesh classify --staged --fail-on-private --fail-on-review
```

If this fails, either sanitize or move the artifact to private storage.

For deterministic release automation (classification + weave + optional witness/tests):

```bash
agentmesh release-check --staged --require-witness --json
```

To publish alpha gate evidence safely:

```bash
agentmesh sanitize-alpha-gate-report \
  --in .agentmesh/runs/alpha-gate-report.json \
  --out docs/alpha-gate-report.public.json
```

## CI Rollout Pattern

1. Start with PR preview mode (non-blocking) and collect artifacts.
2. Triage common review/private hits and tune policy globs.
3. Flip to blocking once false positives are under control.

Current repo setup follows this pattern:

- `public-private-guard` blocks private artifacts on PRs.
- `release-check-preview` runs in non-blocking mode and uploads `.release-check.json`.
