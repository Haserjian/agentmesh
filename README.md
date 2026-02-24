# AgentMesh

Local-first multi-agent coordination and provenance for coding workflows.

AgentMesh adds deterministic coordination (claims, waits, steals), commit-linked lineage (`AgentMesh-Episode` trailers + weave events), and portable handoff bundles (`.meshpack`) on top of normal git workflows.

## Install

```bash
pipx install agentmesh-core   # recommended (isolated)
# or
pip install agentmesh-core

# optional: commit witness signing (Ed25519)
pipx install "agentmesh-core[witness]"
```

## Quick Start

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

When multiple AI agents (or humans) work in the same repo, AgentMesh prevents chaos:

- **Claims**: agents lock files/ports/resources before editing. Conflicts are blocked, not merged.
- **Episodes**: every work session gets a unique ID (`ep_...`) that binds claims, capsules, and commits.
- **Capsules**: structured context bundles (SBAR format) for zero-ramp-up handoffs between agents.
- **Weaver**: hash-chained provenance linking capsules to git commits. Every change is traceable.
- **Commit trailers**: `agentmesh commit` injects `AgentMesh-Episode:` by default, and can attach signed witness trailers (`AgentMesh-KeyID`, `AgentMesh-Witness`, `AgentMesh-Sig`) when witness support + keys are present.

## Optional Witness Signing

If installed with `agentmesh-core[witness]`, you can sign commit witnesses locally:

```bash
agentmesh key generate
agentmesh commit -m "Fix timeout handling"
agentmesh witness verify HEAD
```

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
      - uses: Haserjian/agentmesh-action@v1
```

The action posts a sticky PR comment showing what % of commits carry episode trailers. Set `require-trailers: "true"` to enforce 100% coverage.

## License

MIT
