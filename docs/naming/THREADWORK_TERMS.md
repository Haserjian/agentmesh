# Threadwork Terms

> **Producer/consumer relationship:** Threadwork emits evidence; CCIO admits which claims qualify; Assay packages/verifies evidence into reviewer-ready packets. Threadwork is the producer-side evidence protocol for Assay packets, not a second reviewer-facing product.

Status: proposed naming freeze for migration planning.

This document locks the ontology before any broad rename. It defines the
intended boundary between `Threadwork`, `AgentMesh`, `Assay`, and
`threadstone` so the rename does not sprawl into a vague `Thread*` suite.

## Decision

Canonical public coordination-substrate name: `Threadwork`

Deprecated public name: `AgentMesh`

Current migration posture:
- No flag-day rename
- No immediate implementation-wide rewrite
- `AgentMesh` remains a compatibility alias during the migration window

## Core Terms

### Threadwork

`Threadwork` is the governed coordination and provenance substrate for
delegated work.

It owns:
- coordination of delegated work
- continuity across handoffs
- execution provenance
- live work structure around claims, episodes, and capsules

It does not own:
- proof-pack verification
- trust verdicts
- reviewer-facing settlement artifacts

### thread

A `thread` is the live unit of coordinated delegated work.

It may span:
- one or more episodes
- multiple claims
- multiple commits
- multiple handoffs

Use `thread` for the running work object, not for the durable promoted
artifact.

### threadlog

A `threadlog` is the chronological execution/provenance log for a thread.

Use this term for:
- ordered events
- handoff history
- provenance trails
- continuity records

Do not use it as a synonym for a proof pack or a verdict.

### threadflow

`threadflow` is optional vocabulary for the handoff or execution path of a
thread.

Use it only if needed to distinguish:
- the running path of delegated work
- from the chronological log (`threadlog`)

Do not force this term into the public surface unless it solves a concrete
ambiguity.

### threadstone

A `threadstone` is a durable promoted representation of a completed or
significant thread, used for continuity, retrieval, and MemoryGraph anchoring.

Threadstones are minted, not run.

Threadstones are not:
- the coordination substrate
- the runtime
- the worker mesh
- the proof pack
- the verdict

Invariant:
- All threadstones are promoted thread summaries.
- Some threadstones may also qualify as high-coherence wisdom nodes.

This prevents `threadstone` from expanding into a vague synonym for every
meaningful artifact.

## Assay Boundary

`Assay` remains the trust and proof layer.

Assay owns:
- receipts
- proof packs
- reviewer packets
- verdicts

Threadwork produces provenance.
Assay evaluates proof.
MemoryGraph preserves threadstones.
Guardian constrains execution.
Execution Spine runs the work.

## Compatibility Notes

During the migration window:
- `AgentMesh` may remain in CLI, config, schema, trailer, and state-path
  compatibility surfaces
- `Threadwork` is the canonical user-facing name
- new public narrative should prefer `Threadwork`
- old implementation names may persist temporarily where invasive renames would
  create unnecessary risk

This is a naming and ontology freeze, not an implementation freeze.

## Non-Goals

This document does not:
- rename all internals immediately
- rename Assay receipts into Threadwork language
- create a broad `Thread*` vocabulary family
- require every existing `mesh` internal to be renamed on sight

Restraint is intentional. `Threadwork` becomes stronger when it stays boring.

## Guidance

Prefer:
- `Threadwork` for the substrate
- `thread` for the live work unit
- `threadlog` for chronological provenance
- `threadstone` for promoted durable thread summaries
- `Assay receipt` / `Assay proof pack` / `Assay verdict` for trust artifacts

Avoid introducing new user-facing terms such as:
- `Threadmark`
- `Threadcraft`
- `Threadfile`
- `Threadyield`
- `Threadply`
- `Threadwarp`

unless a future slice proves that one maps to a distinct layer that cannot be
described clearly with the existing vocabulary.
