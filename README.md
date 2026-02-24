# AgentMesh

Local-first multi-agent coordination and provenance for coding workflows.

AgentMesh adds deterministic coordination (claims, waits, steals), commit-linked lineage (`AgentMesh-Episode` trailers + weave events), and portable handoff bundles (`.meshpack`) on top of normal git workflows.

## Install

```bash
pip install agentmesh-core
```

## Quick Start

```bash
# in your repo
agentmesh init --install-hooks

agentmesh task start --title "Fix login timeout" \
  --claim src/auth.py --claim tests/test_auth.py

# edit + stage as normal
git add src/auth.py tests/test_auth.py

agentmesh task finish --message "Fix login timeout handling" \
  --run-tests "pytest -q tests/test_auth.py"
```

## CI Integration

This repo uses [`Haserjian/agentmesh-action@v1`](https://github.com/Haserjian/agentmesh-action) for PR lineage coverage.

```yaml
name: Lineage Check

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
      - uses: Haserjian/agentmesh-action@v1
        with:
          require-trailers: "false"  # set to "true" to enforce 100% coverage
```

The check reads commit trailers in the PR range and reports lineage coverage in both the Action summary and a sticky PR comment.
