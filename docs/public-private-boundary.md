# Public / Private Boundary

AgentMesh separates staged artifacts into three publication classes:

- `public`: safe to publish in repo docs, PR comments, release notes, or exported bundles.
- `private`: raw run output, unredacted evidence, ephemeral logs, or anything that should not leave the private worktree without sanitization.
- `review`: ambiguous cases that need a human decision before publication.

## Current Authority

Use these surfaces for current classification and release decisions:

- `agentmesh classify --staged --json` for staged-file classification.
- `agentmesh release-check --staged --json` before release or PR finalization.
- `trust/signers.yaml` and `trust/acceptance.yaml` for signer and decision policy.
- `trust/BYPASS_POLICY.md` for the narrow direct-to-main evidence-commit exception.

## Boundary Rules

- Raw canary artifacts stay `private` until sanitized.
- Evidence or proof outputs may become `public` only after the report is sanitized for publication.
- Workflow and trust-policy changes remain PR-reviewed; admin bypass is reserved for evidence commits and collector fixes described in `trust/BYPASS_POLICY.md`.
- `review` means the artifact can exist in the repo, but it should not be published until a person explicitly classifies it.

## Examples

- Public: README examples, sanitized proof summaries, publishable docs.
- Private: canary bundles, raw witness payloads, unredacted alpha-gate outputs.
- Review: mixed bundles, boundary cases, or artifacts that need a manual publication decision.

## Why This Exists

The README and alpha-gate materials reference this boundary because the repo needs a single canonical explanation of what may be published and what must stay private.
