# Threadwork Rename Memo

Status: proposed migration memo for `AgentMesh` -> `Threadwork`.

## Decision

`Threadwork` is the canonical public name for the coordination and provenance
substrate.

`AgentMesh` is deprecated as a public name and remains a compatibility alias
during the migration window.

## Scope

This memo covers public naming migration only.

It does not rename code immediately. It defines how to move safely from
`AgentMesh` to `Threadwork` without a flag day.

Policy:
- `Threadwork` is the canonical public name.
- `AgentMesh` remains a compatibility alias during migration.
- No flag-day rename.
- No proof-layer rename.
- `Assay` keeps receipts, proof packs, reviewer packets, and verdicts.
- Existing implementation names may persist temporarily where rename risk is
  high.

## Compatibility Matrix

| Surface | Migration policy now | End-state target | Notes |
|---|---|---|---|
| README/docs narrative | Rename now | `Threadwork` | First public surface to change |
| CLI command names | Alias later | `threadwork` canonical, `agentmesh` accepted during window | Avoid breaking existing scripts |
| Config keys | Alias later | `threadwork.*` or neutral keys where justified | Keep old keys readable during window |
| Schema enum/string values | Preserve initially | Review case-by-case | High risk for compatibility and receipts |
| Trailers / receipts | Alias later | New emission may move to `Threadwork-*`; old `AgentMesh-*` must remain parseable | History must stay readable |
| State paths such as `.agentmesh` | Preserve initially | Review later | High migration risk; do not break local state |
| GitHub action / repo naming | Rename narrative first, implementation later | `threadwork` naming where practical | Keep existing references working until coordinated cut |
| Python package/module paths | Preserve initially | Review later | Highest-risk implementation surface |

## Migration Phases

### Phase 1: naming freeze

- Freeze ontology and public naming
- Publish terms and migration memo
- Stop debating umbrella vocabulary

### Phase 2: docs and narrative

- Update README and docs to prefer `Threadwork`
- Add “formerly AgentMesh” language where needed
- Keep proof-layer language under `Assay`

### Phase 3: compatibility aliases

- Add `Threadwork` as canonical public term
- Keep `agentmesh` CLI/config/trailer/state compatibility
- Do not remove old readers during this phase

### Phase 4: implementation review

- Inventory public `AgentMesh` surfaces
- Classify each as rename now, alias later, preserve internal, or risky
- Rename only low-risk surfaces with explicit receipts

### Phase 5: deprecation and removal

- Stop emitting deprecated names first
- Continue reading old names for a longer window
- Remove old names only after compatibility evidence is clear

## Non-Goals

- No immediate invasive rename of internals
- No rename of `Assay` receipts, proof packs, reviewer packets, or verdicts
- No forced rename of every internal `mesh` term
- No migration of `.agentmesh` or other state paths until explicitly reviewed
- No accidental staging of `.loom/` or `data/`

## Review Checklist

- Does the change make `Threadwork` the canonical public name?
- Does it preserve `AgentMesh` as a compatibility alias where breakage risk is
  high?
- Does it avoid renaming the proof layer?
- Does it avoid touching state paths unless explicitly planned?
- Does it keep old trailers, schemas, and receipts readable?
- Does it stage only intended docs and not runtime artifacts?
