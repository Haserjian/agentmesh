# Pilot Success Scorecard

Measurable criteria for evaluating a proof-carrying PR pilot. Fill this in during and after the pilot period.

---

## Pilot Info

| Field | Value |
|-------|-------|
| Repository | |
| Start date | |
| End date | |
| PRs through gate | / target: 5 |
| Pilot lead | |

---

## Success Criteria

### Must-pass (all required)

| # | Criterion | Target | Actual | Pass? |
|---|-----------|--------|--------|-------|
| 1 | Time to first proof-carrying PR | < 60 min from start | | |
| 2 | Since-enforcement coverage | >= 95% of PRs carry all 3 checks | | |
| 3 | False-block rate | < 5% of PRs blocked incorrectly | | |
| 4 | CI latency (p95) | < 3 min total for all 3 checks | | |

### Should-pass (important but not blocking)

| # | Criterion | Target | Actual | Pass? |
|---|-----------|--------|--------|-------|
| 5 | Zero manual intervention after setup | 0 manual fixes needed | | |
| 6 | All artifacts downloadable | 100% of runs produce gate + verify artifacts | | |
| 7 | No unexpected warnings after day 1 | No recurring non-actionable warnings in pilot checks | | |
| 8 | Developer satisfaction | "Would keep it enabled" from pilot lead | | |

---

## Per-PR Log

Record each PR that goes through the gate during the pilot.

| PR # | Date | lineage | assay-gate | assay-verify | Latency | Notes |
|------|------|---------|------------|--------------|---------|-------|
| | | PASS/FAIL | PASS/FAIL | PASS/FAIL | s | |
| | | PASS/FAIL | PASS/FAIL | PASS/FAIL | s | |
| | | PASS/FAIL | PASS/FAIL | PASS/FAIL | s | |
| | | PASS/FAIL | PASS/FAIL | PASS/FAIL | s | |
| | | PASS/FAIL | PASS/FAIL | PASS/FAIL | s | |

---

## Incident Log

Any false blocks, unexpected failures, or required manual intervention.

| Date | PR # | Issue | Resolution | Time to fix |
|------|------|-------|------------|-------------|
| | | | | |

---

## Summary

| Question | Answer |
|----------|--------|
| All must-pass criteria met? | YES / NO |
| Recommend enabling permanently? | YES / NO / CONDITIONAL |
| Recommended policy level | baseline / strict / enterprise |
| Recommended min-score | |
| Issues to resolve before rollout | |

---

## Pilot → Production Checklist

After a successful pilot:

- [ ] All must-pass criteria met
- [ ] Incident log reviewed, no unresolved issues
- [ ] Branch protection rules set: lineage + assay-gate + assay-verify required
- [ ] Team notified of enforcement date
- [ ] Workflow committed to default branch
- [ ] Score baseline saved (`assay gate save-baseline . --json`)
- [ ] Optional: raise `--min-score` to current score minus 5 (regression buffer)
- [ ] Optional: upgrade from `baseline` to `strict` policy profile
