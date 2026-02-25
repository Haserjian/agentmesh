# Failure Modes and Fixes

What can go wrong with proof-carrying PR checks, why, and how to fix it.

---

## lineage check

### "Coverage: 0%"

**Why**: No commits in the PR have `AgentMesh-Episode:` trailers. This is normal for commits made without AgentMesh.

**Fix**: This is expected on first adoption. In `baseline` mode, this is advisory only (the check still passes). To add trailers, use `agentmesh commit` instead of `git commit`, or add trailers manually:

```bash
git commit -m "your message" --trailer "AgentMesh-Episode: ep_$(uuidgen | tr -d '-' | head -c 12)"
```

### "FAIL: lineage coverage below 100%"

**Why**: You're using `policy-profile: 'strict'` or `'enterprise'` and some commits lack trailers.

**Fix**: Either switch to `baseline` mode, or ensure all commits go through `agentmesh commit`. Interactive rebases and fixup commits will lose trailers -- re-apply them after rebase.

### "Could not post PR comment"

**Why**: The workflow token lacks `pull-requests: write` permission. Common on fork PRs.

**Fix**: This is cosmetic -- the check still runs and results appear in the step summary. To fix, ensure the workflow has:

```yaml
permissions:
  pull-requests: write
```

---

## assay-gate

### "Exit code 3: bad input"

**Why**: `assay gate check` couldn't find recognizable evidence in the repo path.

**Fix**: Make sure the checkout step ran (`actions/checkout@v4`). If the repo is empty or has no Python/JS/config files, assay may have nothing to score. This is not a failure -- it means evidence scoring is not applicable yet.

### "Score regression detected"

**Why**: The repo's evidence score dropped below the saved baseline.

**Fix**: Run `assay gate check . --json` locally to see what changed. Common causes:
- Deleted test files
- Removed CI configuration
- Changed directory structure

To reset the baseline: `assay gate save-baseline . --json`

### "pip install assay-ai failed"

**Why**: Network issue or PyPI outage.

**Fix**: Retry the workflow. If persistent, pin a specific version:

```yaml
- run: pip install "assay-ai==1.10.1"
```

### "Timeout"

**Why**: `assay gate check` took longer than expected on a very large repo.

**Fix**: The default assay timeout is 30s. For large repos, this is rarely hit. If it happens consistently, check for extremely deep directory trees or large binary files.

---

## assay-verify

### "No proof pack found"

**Why**: The `assay run` command failed to produce a proof pack.

**Fix**: Check the `run.json` output in the artifacts. Common causes:
- The wrapped command failed (the `echo` canary should never fail -- if it does, the runner has issues)
- Disk full or permissions issue on the runner

### "verify-pack: FAIL (integrity)"

**Why**: The proof pack's hash chain was broken. This should never happen in normal operation.

**Fix**: This indicates either a bug or actual tampering. Check:
1. Is anything modifying files in `.assay-verify/` between `assay run` and `assay verify-pack`?
2. Are there concurrent workflows writing to the same paths?
3. File an issue if reproducible: https://github.com/Haserjian/assay/issues

### "verify-pack: FAIL (claims)"

**Why**: A behavioral claim check failed.

**Fix**: The starter workflow uses `--allow-empty` and no claim flags, so this shouldn't happen with the default template. If you've added `-c` flags, check which claims are failing in the verify output.

---

## Branch protection

### "Required check not found"

**Why**: You added `lineage`, `assay-gate`, or `assay-verify` as a required check, but the check name doesn't match the job name in the workflow.

**Fix**: The check names in GitHub come from the workflow's `jobs:` keys. Ensure they match exactly:
- Workflow job key: `lineage` --> required check name: `lineage`
- Workflow job key: `assay-gate` --> required check name: `assay-gate`
- Workflow job key: `assay-verify` --> required check name: `assay-verify`

### "Check stuck pending"

**Why**: The workflow didn't trigger. This happens when the workflow file isn't on the PR's base branch.

**Fix**: Merge the workflow file to `main` first (directly or via a bootstrap PR), then open new PRs.

---

## General

### "All checks pass but I see DEGRADED in artifacts"

**Why**: DEGRADED means assay ran but couldn't find optimal evidence. The check still passes -- DEGRADED is informational, not a failure.

**Fix**: No action needed. As you add more tests, CI config, and documentation, the evidence score will increase naturally.

### "False block: check failed but the code is fine"

**Why**: Evidence checks assess the *evidence around* the code, not the code itself. A failing gate means the evidence quality is below the threshold, not that the code is broken.

**Fix**:
- If `--min-score 0`: The gate should never block. File an issue.
- If `--min-score N > 0`: Your evidence score dropped. Run `assay gate check . --json` locally to diagnose.
- Temporary override: Remove the check from required status checks, merge, re-add.

### "CI is slow"

**Expected latency**:
- lineage: < 10s (pure bash + git)
- assay-gate: 20-40s (install + score)
- assay-verify: 30-60s (install + run + verify)

**If slower**: Check runner queue time (not action time). The assay install is cached by pip after the first run.
