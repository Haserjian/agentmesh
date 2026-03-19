# Direct-to-main Push Policy

Evidence commits (artifact bundles, canary results, collector fixes) may use
admin bypass to push directly to main without PR review. This is intentional.

## Why bypass is needed

1. Evidence commits capture post-merge artifacts — the PR they document is
   already merged, so they cannot go through PR flow themselves.
2. Collector script fixes discovered during canary runs need immediate landing
   to keep the evidence pipeline coherent within a session.
3. Requiring a PR for evidence commits creates a chicken-and-egg: the canary
   proves the lane, but the canary evidence can't use the lane it's proving.

## Conditions for admin bypass

All of the following must hold:

- Commit contains only: artifact JSON, collector scripts, trust policy docs
- Commit does NOT modify: `src/`, `tests/`, `.github/workflows/`, core trust config
- Commit message starts with `evidence:` or `fix:` (for collector fixes)
- The bypass is visible in git push output (GitHub logs it)

## What this does NOT cover

- Feature or behavior changes MUST go through PR with required checks
- CI workflow changes MUST go through PR
- Trust policy file changes (signers.yaml, acceptance.yaml) MUST go through PR
- Test changes MUST go through PR

## Audit trail

GitHub logs every bypass as `Bypassed rule violations for refs/heads/main`.
That line is intentionally not suppressed. It serves as an audit marker that
the push used an exception path, not the normal constitutional flow.

## Referenced by

Canary artifact bundles may include:
```json
{
  "evidence_commit_method": "admin_bypass",
  "bypass_policy": "trust/BYPASS_POLICY.md"
}
```
