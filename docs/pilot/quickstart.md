# 10-Minute Quickstart: Proof-Carrying PRs

Get your first proof-carrying PR in under 10 minutes. No configuration beyond one workflow file.

## What you get

Every AI-assisted PR will:
1. **Track lineage** -- which commits came from agent sessions
2. **Score evidence quality** -- via `assay gate check`
3. **Verify proof packs** -- tamper-evident records of what your AI did

After enforcement is enabled, if any required check fails, the PR cannot merge.

## Prerequisites

- A GitHub repository where you can edit branch protection rules
- PRs go through CI before merge (standard for any team)

No local tooling required. Everything runs in GitHub Actions.

---

## Step 1: Add the workflow file (2 min)

Copy `.github/workflows/proof-carrying-pr.yml` from the [starter workflow](./starter-workflow.yml) into your repository.

Or create it manually:

```yaml
# .github/workflows/proof-carrying-pr.yml
name: Proof-Carrying PR

on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]

permissions:
  contents: read
  pull-requests: write

jobs:
  lineage:
    runs-on: ubuntu-latest
    steps:
      - name: Check lineage coverage
        uses: Haserjian/agentmesh-action@v2
        with:
          policy-profile: 'baseline'

  assay-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install "assay-ai>=1.10.1"
      - name: Run evidence gate
        run: |
          mkdir -p .assay
          assay gate check . --min-score 0 --json | tee .assay/gate-check.json
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: assay-gate-report
          path: .assay

  assay-verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install "assay-ai>=1.10.1"
      - name: Build and verify proof pack
        run: |
          set -euo pipefail
          mkdir -p .assay-verify
          assay run \
            --allow-empty \
            --output ".assay-verify/proof_pack_ci" \
            --json \
            -- echo "proof-carrying-pr canary" \
            | tee ".assay-verify/run.json"
          assay verify-pack ".assay-verify/proof_pack_ci" --json \
            | tee ".assay-verify/verify.json"
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: assay-verify-report
          path: .assay-verify
```

Commit and push to your default branch.

## Step 2: Open a PR (3 min)

Create a branch, make any change, push, and open a PR. Three checks will run:

| Check | What it does | First-run behavior |
|-------|-------------|-------------------|
| **lineage** | Counts `AgentMesh-Episode:` trailers | Reports 0% coverage (expected for non-AgentMesh commits) |
| **assay-gate** | Scores the repo's evidence quality | Passes with `--min-score 0` (baseline) |
| **assay-verify** | Builds a proof pack and verifies it | Creates a canary pack and self-verifies |

All three should pass. If any fails, see [Failure Modes](./failure-modes.md).

## Step 3: Review the artifacts (2 min)

After CI completes:

1. Go to the PR's **Checks** tab
2. Download the **assay-gate-report** artifact -- this is your gate score
3. Download the **assay-verify-report** artifact -- this is your proof pack
4. Check the PR comment from the lineage action -- this shows coverage %

You now have a proof-carrying PR.

## Step 4: Enable enforcement (3 min)

Once the checks are green, add them as required status checks:

1. Go to **Settings > Branches > Branch protection rules** for `main`
2. Check **Require status checks to pass before merging**
3. Add these as required checks:
   - `lineage`
   - `assay-gate`
   - `assay-verify`
4. Save

Now PRs cannot merge without passing all three evidence checks.

---

## What's next

- **Raise the bar**: Change `policy-profile: 'baseline'` to `'strict'` to require lineage trailers
- **Set a score floor**: Change `--min-score 0` to `--min-score 20` (or higher) to enforce minimum evidence quality
- **Add claim checks**: Use `assay run -c receipt_completeness` to verify specific behavioral claims
- **See failure modes**: [What breaks and how to fix it](./failure-modes.md)
- **Track success**: [Pilot scorecard](./scorecard.md)

## How it works (30-second version)

```
Developer opens PR
    |
    v
lineage check -----> "3/4 commits traced to agent sessions"
    |
assay gate ---------> "evidence score: 42.5 (PASS, min=0)"
    |
assay verify -------> "proof pack integrity: PASS"
    |
    v
PR is proof-carrying --> can merge
```

No code changes. No SDK. No runtime dependency. Pure CI.
