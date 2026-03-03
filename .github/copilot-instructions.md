# Copilot Instructions for AgentMesh

## Codebase overview

- **Language**: Python 3.10+
- **Type hints**: Required (pydantic models throughout)
- **Testing**: pytest; 38 test modules under `tests/`
- **Style**: Black formatter, isort
- **Dependencies**: pydantic>=2.0, typer>=0.9, rich>=13.0
- **Optional**: cryptography (witness signing), mcp (Model Context Protocol)

## Core patterns

### Episodes and claims

Every work session is an **episode**. Before editing a file, **claim** it. This prevents multi-agent conflicts.

```bash
# CLI equivalent
agentmesh episode start --title "my task"
agentmesh claim src/agentmesh/cli.py
# ... work ...
agentmesh episode end

# Or use the happy-path wrapper:
agentmesh task start --title "my task" --claim src/agentmesh/cli.py
agentmesh task finish --message "feat: done"
```

### Provenance (weaver)

The weaver maintains a hash chain linking every change to its predecessor. Do not break the chain — always go through the weaver API.

### Witness signing

Optional Ed25519 signing via `src/agentmesh/witness.py`. When enabled, commits include cryptographic attestation in trailers.

### Public/private classification

All staged files are classified before merge. Private artifacts (credentials, internal configs) are blocked by CI.

```bash
agentmesh classify --staged --fail-on-private
```

## Code standards

- **Imports**: stdlib, third-party, local
- **Models**: Use pydantic BaseModel for all data structures
- **CLI**: Use typer for new commands; match existing patterns in `cli.py`
- **Commits**: conventional format `type(scope): description`
- **Commit trailers**: Include `AgentMesh-Episode: <id>` when working in an episode

## When to flag for human review

- Changes to `witness.py` or signing logic
- Changes to `public_private.py` classification rules
- New CLI commands or breaking changes to existing ones
- Changes to `weaver.py` hash chain logic
- CI/CD workflow modifications
- Schema changes to episode/claim/capsule models
