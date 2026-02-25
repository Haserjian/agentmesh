# Evidence Wire Protocol v0

**Status:** DRAFT
**Date:** 2026-02-25
**Scope:** Canonical identity envelope + receipt interop across AgentMesh, Assay, CCIO, Quintet

---

## Problem

Four repos emit receipts/events with overlapping but incompatible identity schemes:

| Repo | Primary ID | Chain | Signing | Storage |
|------|-----------|-------|---------|---------|
| AgentMesh | `task_id` + `sequence_id` | SHA-256 sorted JSON | Ed25519 witness trailers | SQLite WAL + JSONL |
| Assay | `receipt_id` + `seq` | Merkle tree (JCS) | Ed25519 proof packs | JSONL (O_APPEND + flock) |
| CCIO | `receipt_id` + CloudEvents | `parent_hashes` list | Per-domain | JSONL stream |
| Quintet | `quintet_run_id` | None | None | JSON export |

Cross-repo queries ("which policy was active when this task merged?") require manual ID chasing across four different schemas.

## Design Constraints

1. **No shared database.** Repos communicate via flat files and subprocess calls. This is a pinned decision.
2. **No breaking changes.** Existing schemas must remain valid. The envelope is additive.
3. **Optional adoption.** Each repo can adopt the envelope incrementally.
4. **Deterministic.** No fields that require network calls or external state to populate.

---

## 1. Identity Envelope v0

A set of **optional fields** that any receipt, event, or artifact can carry to enable cross-repo correlation.

```json
{
  "_ewp_version": "0",
  "_ewp_correlation_id": "<string>",
  "_ewp_task_id": "<string>",
  "_ewp_episode_id": "<string>",
  "_ewp_agent_id": "<string>",
  "_ewp_policy_version": "<string>",
  "_ewp_context_hash": "<string>",
  "_ewp_parent_ids": ["<receipt_id | event_id>"],
  "_ewp_origin": "<repo_name>/<module>"
}
```

### Field Definitions

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `_ewp_version` | string | Yes (if any `_ewp_*` present) | Protocol version. Always `"0"` for v0. |
| `_ewp_correlation_id` | string | No | Opaque string linking related operations across repos. Propagated, never generated mid-chain. |
| `_ewp_task_id` | string | No | AgentMesh `task_id`. Present when operation is task-scoped. |
| `_ewp_episode_id` | string | No | Episode ID (AgentMesh or CCIO). Present when operation is episode-scoped. |
| `_ewp_agent_id` | string | No | Agent that performed the operation. |
| `_ewp_policy_version` | string | No | Hash or version string of active policy at emission time. |
| `_ewp_context_hash` | string | No | SHA-256 of input context (spawner prompt, tool call args, etc.). |
| `_ewp_parent_ids` | string[] | No | Receipt/event IDs this record depends on. Cross-repo references use `<repo>:<id>` format. |
| `_ewp_origin` | string | No | `<repo>/<module>` that emitted this record. E.g., `agentmesh/assay_bridge`. |

### Conventions

- All `_ewp_*` fields use underscore prefix to avoid collision with domain fields.
- Fields are **nullable by omission** -- absent means unknown/not applicable.
- `_ewp_parent_ids` uses qualified format for cross-repo: `assay:r_a1b2c3d4e5f6`, `agentmesh:evt_000042`.
- Timestamps follow ISO 8601 with explicit UTC: `YYYY-MM-DDTHH:MM:SS.ffffffZ`.

---

## 2. Adoption Per Repo

### AgentMesh

**Where:** `events.append_event()` payload, `assay_bridge.emit_bridge_event()` payload, `weaver.append_weave()` fields.

**Phase 1 (v0):**
- Add `_ewp_task_id`, `_ewp_episode_id`, `_ewp_agent_id` to ASSAY_RECEIPT payloads (already present as `task_id`, `agent_id`; add `_ewp_` prefixed copies for protocol compliance).
- Add `_ewp_origin: "agentmesh/assay_bridge"` to bridge events.
- Propagate `_ewp_correlation_id` from task meta if present.

**Phase 2:**
- Add `_ewp_context_hash` from spawner's `context_hash` to weave events.
- Set `ASSAY_TRACE_ID` env var in spawned workers for Assay trace correlation.

### Assay

**Where:** `store.emit_receipt()` data fields.

**Phase 1 (v0):**
- Accept and persist `_ewp_*` fields in receipt data without schema validation.
- Add `_ewp_origin: "assay/store"` to emitted receipts.
- Pass `_ewp_correlation_id` through MCP proxy tool call receipts.

**Phase 2:**
- Include `_ewp_*` fields in proof pack `build_metadata.json`.
- Gate report includes `_ewp_task_id` when invoked via bridge.

### CCIO

**Phase 1 (v0):**
- Add `_ewp_episode_id` and `_ewp_policy_version` to `ChiRoutingReceipt`.
- Episode export includes `_ewp_correlation_id` in metadata when available.

### Quintet

**Phase 1 (v0):**
- `PolicyRecommendation` gains `_ewp_correlation_id` and `_ewp_parent_ids` (list of `episode_id`s that informed the recommendation).
- `quintet_run_id` becomes the `_ewp_correlation_id` for all downstream effects.

---

## 3. Receipt Type Registry

Receipts that cross repo boundaries. Each has a canonical emitter and expected consumers.

| Receipt Type | Emitter | Consumers | Trigger |
|-------------|---------|-----------|---------|
| `ASSAY_RECEIPT` | AgentMesh (bridge) | Assay (gate), KPI | Terminal task transition |
| `TASK_TRANSITION` | AgentMesh (orchestrator) | KPI, alpha gate | Any state change |
| `WEAVE_EVENT` | AgentMesh (weaver) | Weave verifier | Any state change |
| `POLICY_RECOMMENDATION` | Quintet | CCIO, AgentMesh | Policy analysis run |
| `POLICY_APPLY` | Consumer repo | Quintet (feedback) | Policy change applied |
| `POLICY_ROLLBACK` | Consumer repo | Quintet (feedback) | Policy change reverted |
| `CHECKPOINT_RECEIPT` | AgentMesh (future) | Assay, KPI | Non-terminal critical transitions |

### New Receipt Types (v0 additions)

**`POLICY_RECOMMENDATION`** -- emitted by Quintet when analysis produces a recommendation.
```json
{
  "type": "POLICY_RECOMMENDATION",
  "_ewp_version": "0",
  "_ewp_correlation_id": "qt_brain_temperature_20260225_120000",
  "_ewp_parent_ids": ["ccio:ep_abc123", "ccio:ep_def456"],
  "_ewp_policy_version": "sha256:a1b2c3...",
  "_ewp_origin": "quintet/policies",
  "lever": "BRAIN_TEMPERATURE",
  "action": "PROMOTE",
  "current_value": 0.7,
  "recommended_value": 0.65,
  "confidence": 0.82,
  "episodes_analyzed": 47
}
```

**`POLICY_APPLY`** -- emitted by consuming repo when a policy recommendation is enacted.
```json
{
  "type": "POLICY_APPLY",
  "_ewp_version": "0",
  "_ewp_correlation_id": "qt_brain_temperature_20260225_120000",
  "_ewp_parent_ids": ["quintet:qt_brain_temperature_20260225_120000"],
  "_ewp_policy_version": "sha256:d4e5f6...",
  "_ewp_origin": "ccio/guardian",
  "lever": "BRAIN_TEMPERATURE",
  "old_value": 0.7,
  "new_value": 0.65,
  "applied_at": "2026-02-25T13:00:00Z"
}
```

**`POLICY_ROLLBACK`** -- emitted when a policy change is reverted.
```json
{
  "type": "POLICY_ROLLBACK",
  "_ewp_version": "0",
  "_ewp_correlation_id": "qt_brain_temperature_20260225_120000",
  "_ewp_parent_ids": ["ccio:policy_apply_xyz"],
  "_ewp_origin": "ccio/guardian",
  "lever": "BRAIN_TEMPERATURE",
  "reverted_to": 0.7,
  "reason": "dignity_score regression in canary cohort"
}
```

---

## 4. Compatibility Test Matrix

Tests that prove cross-repo identity correlation works. Each test is implementable as a pytest fixture + assertion.

### T1: AgentMesh bridge event carries task identity

**Location:** `agentmesh/tests/test_ewp_compat.py`
**Setup:** Create task, advance to MERGED.
**Assert:**
- ASSAY_RECEIPT event payload contains `_ewp_task_id` == `task.task_id`
- ASSAY_RECEIPT event payload contains `_ewp_episode_id` (if task has one)
- ASSAY_RECEIPT event payload contains `_ewp_origin` == `"agentmesh/assay_bridge"`

### T2: AgentMesh weave events carry episode linkage

**Location:** `agentmesh/tests/test_ewp_compat.py`
**Setup:** Start episode, create task in episode, transition task.
**Assert:**
- All TASK_TRANSITION weave events carry `episode_id` matching the active episode.
- Weave events for episode-scoped tasks have monotonic `sequence_id`.

### T3: Assay gate report includes task context when invoked via bridge

**Location:** `agentmesh/tests/test_ewp_compat.py`
**Setup:** Mock `assay gate check` returning a report with `_ewp_task_id` in metadata.
**Assert:**
- Bridge result's `gate_report` preserves `_ewp_*` fields from assay output.

### T4: PolicyRecommendation carries correlation chain

**Location:** `puppetlabs/tests/test_ewp_compat.py`
**Setup:** Create PolicyRecommendation with `_ewp_correlation_id` and `_ewp_parent_ids`.
**Assert:**
- Serialized recommendation preserves all `_ewp_*` fields.
- `_ewp_parent_ids` references valid episode IDs.

### T5: Round-trip identity through episode export

**Location:** `ccio/tests/test_ewp_compat.py` (or integration test)
**Setup:** CCIO episode with `_ewp_correlation_id` in metadata → export JSON → Quintet `LoomEpisode` ingestion.
**Assert:**
- Quintet's `LoomEpisode` preserves `_ewp_correlation_id` from CCIO export.
- `receipt_ids` in export are qualified: `ccio:<receipt_id>`.

### T6: End-to-end correlation chain

**Location:** `agentmesh/tests/test_ewp_e2e.py` (integration)
**Setup:** Full lifecycle: task created → assigned → running → merged → bridge fires → mock Quintet recommendation references task.
**Assert:**
- Every event/receipt in the chain shares the same `_ewp_correlation_id`.
- `_ewp_parent_ids` form a DAG (no cycles, no orphans within the chain).

### T7: Unknown _ewp_ fields are preserved, not dropped

**Location:** Each repo's test suite.
**Setup:** Inject receipt/event with `_ewp_future_field: "test"`.
**Assert:**
- Field survives serialization, storage, and retrieval.
- No validation error on unknown `_ewp_*` fields.

### T8: Absent _ewp_ fields don't break existing code

**Location:** Each repo's test suite.
**Setup:** Emit receipt/event with zero `_ewp_*` fields.
**Assert:**
- All existing tests still pass.
- No KeyError or missing field exceptions.

---

## 5. Migration Path

### Week 1: Schema + Tests
1. Add `_ewp_*` fields to AgentMesh ASSAY_RECEIPT payload (additive, no breaking change).
2. Write T1, T2, T7, T8 in AgentMesh test suite.
3. Add `_ewp_*` pass-through test in Assay (T7, T8).

### Week 2: Cross-Repo Linkage
4. CCIO episode export includes `_ewp_episode_id` and `_ewp_correlation_id`.
5. Quintet `PolicyRecommendation` gains `_ewp_*` fields.
6. Write T4, T5.

### Week 3: Policy Receipts
7. Define `POLICY_RECOMMENDATION`, `POLICY_APPLY`, `POLICY_ROLLBACK` receipt types.
8. Write emitter in Quintet, consumer in CCIO.
9. Write T6 end-to-end test.

### Week 4: Checkpoint Receipts
10. Add `CHECKPOINT_RECEIPT` for non-terminal critical transitions in AgentMesh.
11. Expand bridge to emit checkpoint at `ASSIGNED`, `RUNNING`, `CI_PASS`, `REVIEW_PASS`.
12. Update KPI to track checkpoint coverage.

---

## 6. Non-Goals for v0

- **Schema migration of existing data.** Old events/receipts without `_ewp_*` fields remain valid.
- **Central registry service.** No lookup server. Correlation is by convention + flat file.
- **Cryptographic binding between repos.** Each repo's chain integrity is independent. Cross-repo references are pointers, not cryptographic commitments (that's v1).
- **Real-time streaming.** v0 is batch/CI oriented. Streaming is post-adoption.

---

## Appendix: Current ID Formats

| Repo | ID | Format | Example |
|------|-----|--------|---------|
| AgentMesh | task_id | `task_{uuid4[:12]}` | `task_8c69bd04b163` |
| AgentMesh | event_id | `evt_{seq:06d}` | `evt_000042` |
| AgentMesh | weave_id | `weave_{uuid4[:12]}` | `weave_a1b2c3d4e5f6` |
| AgentMesh | spawn_id | `spawn_{uuid4[:12]}` | `spawn_f1e2d3c4b5a6` |
| AgentMesh | episode_id | `ep_{uuid4[:24]}` | `ep_019c937df84aa1eb219e35a7` |
| AgentMesh | attempt_id | `att_{uuid4[:12]}` | `att_a1b2c3d4e5f6` |
| Assay | receipt_id | `r_{uuid4[:12]}` | `r_a1b2c3d4e5f6` |
| Assay | trace_id | `trace_{ts}_{uuid4[:8]}` | `trace_20260225T120000_a1b2c3d4` |
| CCIO | receipt_id | UUID (domain-specific) | `3f2504e0-4f89-...` |
| Quintet | run_id | `qt_{lever}_{ts}` | `qt_brain_temperature_20260225_120000` |
