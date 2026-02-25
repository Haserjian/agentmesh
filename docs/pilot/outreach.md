# Pilot Outreach Templates

Copy-paste messages for recruiting pilot participants.

---

## Short message (Slack/Discord/DM)

```
Hey -- we built an open-source CI layer that makes AI-assisted PRs prove themselves
before merge. No SDK, no runtime dependency -- just one workflow file.

Looking for 1-2 teams to pilot it on a real repo (3-5 PRs over a week).
Setup takes <10 minutes. We handle any issues.

Interested? I can send the quickstart: https://github.com/Haserjian/agentmesh/tree/main/docs/pilot
```

---

## Email (longer form)

**Subject**: Pilot: proof-carrying PRs for AI-assisted code (10 min setup)

```
Hi [name],

We're looking for 1-2 teams to pilot proof-carrying PRs -- a CI-only approach
to governance for AI-assisted code changes.

THE PROBLEM

Teams using AI coding agents (Copilot, Cursor, Claude Code, etc.) have no way
to distinguish AI-generated PRs from human ones, no evidence trail for what the
AI did, and no enforceable quality gate beyond standard tests.

WHAT WE BUILT

One GitHub Actions workflow file that adds three checks to every PR:

  1. Lineage -- tracks which commits came from agent sessions
  2. Evidence gate -- scores the PR's evidence quality (tests, CI, docs)
  3. Proof verification -- builds and verifies a tamper-evident proof pack

If a required check fails, the PR can't merge.

No SDK. No runtime dependency. No code changes. Pure CI.

THE ASK

- Pick one repo (can be internal or OSS)
- Copy our starter workflow file into .github/workflows/
- Run 3-5 PRs through the gate over ~1 week
- Tell us what broke and what was useful

Setup takes <10 minutes. We have a quickstart guide, starter workflow,
failure modes doc, and success scorecard ready.

SUCCESS BAR

- Time to first proof-carrying PR: < 60 minutes
- Since-enforcement coverage: >= 95%
- False-block rate: < 5%
- CI latency: < 3 minutes

LINKS

- Quickstart: https://github.com/Haserjian/agentmesh/tree/main/docs/pilot/quickstart.md
- Starter workflow: https://github.com/Haserjian/agentmesh/tree/main/docs/pilot/starter-workflow.yml
- Failure modes: https://github.com/Haserjian/agentmesh/tree/main/docs/pilot/failure-modes.md
- Assay (evidence compiler): https://github.com/Haserjian/assay
- AgentMesh action: https://github.com/Haserjian/agentmesh-action

Happy to set up a 15-minute call or just async in a thread.

[your name]
```

---

## Case study template (post-pilot)

Use this to document pilot results for publishing.

```markdown
# Case Study: [Team/Repo Name]

## Context
- Team size:
- Repo type: (OSS / internal / monorepo / microservice)
- AI tools in use: (Copilot / Cursor / Claude Code / other)
- PRs per week:

## Setup
- Time to first proof-carrying PR:
- Configuration: (baseline / strict / enterprise)
- Min score threshold:

## Results (over [N] PRs, [N] days)

| Metric | Value |
|--------|-------|
| PRs through gate | |
| Since-enforcement coverage | |
| False-block rate | |
| Average CI latency | |
| Manual interventions needed | |

## What worked
-

## What didn't
-

## Developer feedback
> [quote from pilot lead]

## Outcome
- [ ] Kept enabled after pilot
- [ ] Raised enforcement level
- [ ] Rolled out to additional repos
```

---

## Onboarding checklist (send after they agree)

```markdown
## Proof-Carrying PR Pilot -- Onboarding Checklist

Welcome! Here's everything you need.

### Before you start (5 min)
- [ ] Pick the target repo
- [ ] Confirm branch protection is enabled on the default branch
- [ ] Confirm PRs require status checks to pass before merge

### Setup (10 min)
- [ ] Copy `starter-workflow.yml` to `.github/workflows/proof-carrying-pr.yml`
- [ ] Commit and push to the default branch
- [ ] Open a test PR with any change
- [ ] Confirm all 3 checks appear: lineage, assay-gate, assay-verify
- [ ] Download the gate report artifact and verify it contains JSON

### Day 1
- [ ] All 3 checks passing on test PR
- [ ] Add lineage, assay-gate, assay-verify as required status checks
- [ ] Open your first real PR

### During pilot (3-5 PRs)
- [ ] Log each PR in the scorecard
- [ ] Note any false blocks or unexpected failures
- [ ] Reach out with questions (we're responsive)

### After pilot
- [ ] Fill out the success scorecard
- [ ] Decision: keep / remove / escalate enforcement
- [ ] Optional: share results for case study
```
