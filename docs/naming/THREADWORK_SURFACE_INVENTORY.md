# Threadwork Surface Inventory

Status: inventory-only pass for `AgentMesh` -> `Threadwork`.

Purpose: list public or compatibility-sensitive `AgentMesh` surfaces without
renaming them yet.

Baseline: line-number references in the Evidence column below were captured
against `handoff/m2-switch-2026-04-11` (agentmesh) and the then-current state
of `assay-toolkit` / `assay-internal`. When read against `main` or any branch
without the M2 handoff docs, specific line numbers may drift by a handful of
lines; file paths, surface names, and classifications remain valid.

## Classification Legend

- `rename now`: low-risk public narrative surface; update in docs/name-first phase
- `alias later`: public surface that should gain a `Threadwork` alias before old
  naming is removed
- `preserve internal`: internal or historical surface that can keep old naming
  for now
- `risky/needs review`: high-breakage or contract-sensitive surface; do not
  rename casually

## Inventory

| Surface | Current value | Classification | Evidence | Notes |
|---|---|---|---|---|
| Repo/product narrative | `AgentMesh` | `rename now` | `README.md:1`, `README.md:3`, `README.md:11` | Primary public narrative should move first |
| Cross-repo public docs narrative | `AgentMesh` in Assay docs | `rename now` | `../assay-toolkit/docs/BOUNDARY_MAP.md:9`, `../assay-toolkit/docs/REPO_MAP.md:16`, `../assay-toolkit/docs/security/SECURITY_POSTURE_TODAY.md:96` | Public-facing stack language should converge on `Threadwork` |
| Internal strategy narrative | `AgentMesh` in active strategy docs | `rename now` | `../assay-internal/strategy/STRATEGY_STACK_HIERARCHY.md:13`, `../assay-internal/strategy/COMMERCIAL_DOCTRINE_CANON.md:82` | Rename where these are current doctrine, not historical records |
| CLI command | `agentmesh` | `alias later` | `pyproject.toml:43`, `src/agentmesh/cli.py:23` | `threadwork` should become canonical, `agentmesh` should keep working during migration |
| Secondary CLI entrypoint | `agentmesh-mcp` | `alias later` | `pyproject.toml:44` | Public executable surface; should not disappear without a compatibility window |
| Package install name | `agentmesh-core` | `alias later` | `pyproject.toml:6`, `README.md:5`, `README.md:26` | Public package surface; needs coordination before change |
| Package metadata URLs and badges | GitHub repo/action URLs, PyPI badge | `risky/needs review` | `pyproject.toml:37-40`, `README.md:5` | Depends on repo slug and package naming; update only when target locations are fixed |
| Python package/module path | `src/agentmesh`, `import agentmesh` | `risky/needs review` | `pyproject.toml:59` | High-risk import path; defer until compatibility plan is explicit |
| GitHub repo slug | `Haserjian/agentmesh` | `risky/needs review` | `../assay-toolkit/docs/REPO_MAP.md:16`, `../assay-toolkit/docs/REPO_MAP.md:65` | Repo rename ripples into docs, links, and action references |
| GitHub Action name and refs | `agentmesh-action`, `Haserjian/agentmesh-action@v2` | `alias later` | `README.md:91`, `README.md:106`, `pyproject.toml:40` | Narrative can move first; action refs need a compatibility window |
| Workflow display names and action refs in CI | `AgentMesh lineage coverage`, `Haserjian/agentmesh-action@v1` | `alias later` | `.github/workflows/lineage.yml:15-16` | User-visible CI naming can shift, but action refs are compatibility-sensitive |
| MCP server name | `agentmesh` | `alias later` | `src/agentmesh/mcp_server.py:14` | Integration surface; avoid silent breaking changes |
| CLI metadata/tool id | `tool_name: "agentmesh"` | `alias later` | `src/agentmesh/cli.py:311` | Public-ish integration string; should transition with aliasing |
| Hook marker and hook script names | `agentmesh`, `agentmesh_pre_edit.sh`, `agentmesh_post_edit.sh` | `preserve internal` | `src/agentmesh/hooks/install.py:18`, `src/agentmesh/hooks/install.py:14` | Internal plumbing; low value to rename early |
| Commit trailer keys | `AgentMesh-Episode`, `AgentMesh-KeyID`, `AgentMesh-Witness`, `AgentMesh-Sig`, related chunk keys | `alias later` | `src/agentmesh/cli.py:27`, `src/agentmesh/witness.py:17-23`, `docs/spec/cwe-v1.md:85-93` | New emission can change later, but old history must remain parseable |
| Receipt/provenance export type strings | `agentmesh.weave/v1`, `agentmesh.witness/v1` | `resolved (alias_bug fix, 2026-04-24)` | `src/agentmesh/provenance_export.py:38`, `src/agentmesh/provenance_export.py:88` | Renamed from flat `agentmesh_weave`/`agentmesh_witness` to Assay's namespaced form to clear receipt allowlist rejection. No external consumers found in ccio/loom/assay/claude-organism. |
| Assay bridge/source organ strings | `source_organ: "agentmesh"` | `risky/needs review` | `src/agentmesh/assay_bridge.py:68` | Contract boundary; rename only with explicit receipt compatibility policy |
| Assay schema enum/string values | `"agentmesh"` | `risky/needs review` | `../assay-toolkit/src/assay/decision_receipt.py:65`, `../assay-toolkit/src/assay/schemas/decision_receipt_v0.2.0.schema.json:308`, `../assay-toolkit/src/assay/schemas/claim_assertion.v0.1.schema.json:112` | Public contract surface; may require dual-acceptance for a long window |
| State dir in home | `~/.agentmesh` | `risky/needs review` | `src/agentmesh/keystore.py:17`, `src/agentmesh/events.py:14`, `src/agentmesh/db.py:19` | Do not break local state; likely needs dual-read before any migration |
| Repo-local state dir | `.agentmesh/` | `risky/needs review` | `src/agentmesh/cli.py:96`, `src/agentmesh/public_private.py:81`, `src/agentmesh/worker_adapters.py:254` | Same issue as home dir plus repo policy/config coupling |
| Config file path and prompts | `.agentmesh/policy.json` and related help text | `alias later` | `src/agentmesh/cli.py:241`, `src/agentmesh/cli.py:675`, `src/agentmesh/spawner.py:125` | User-facing path strings can be dual-documented before any storage migration |
| Session path | `~/.agentmesh/.session_id` | `preserve internal` | `src/agentmesh/cli.py:55` | Internal runtime detail; low-value early rename |
| Portable bundle extension | `.meshpack` | `risky/needs review` | `src/agentmesh/passport.py:1`, `src/agentmesh/cli.py:1108`, `README.md:11` | File format surface; no reason to rename until broader compatibility story exists |
| Documentation/spec references for trailers and bundle format | `AgentMesh-*`, `.meshpack` | `alias later` | `docs/spec/cwe-v1.md:85-113`, `docs/pilot/failure-modes.md:11-16` | Specs must reflect old readers and future aliases honestly |
| Public docs/examples with hardcoded repo/action links | `github.com/Haserjian/agentmesh`, `agentmesh-action`, `agentmesh` commands | `rename now` | `docs/pilot/outreach.md:16`, `docs/pilot/outreach.md:68-72`, `.github/copilot-instructions.md:1`, `.github/copilot-instructions.md:20-27` | Narrative/examples should match canonical naming once docs-first rename starts |
| Historical/security docs referencing AgentMesh as past state | `AgentMesh` in audits/adjudications | `preserve internal` | `../assay-toolkit/docs/security/SECURITY_AUDIT_ADJUDICATION_2026-04-03.md:31` | Historical records should not be rewritten casually |

## Recommended Cut Order

1. `rename now`
   - README/docs narrative in this repo
   - active public docs in `assay-toolkit`
   - active strategy/doctrine docs in `assay-internal`

2. `alias later`
   - CLI command
   - GitHub Action naming
   - MCP/tool identifiers
   - trailer emission and docs
   - config path/help text

3. `risky/needs review`
   - package/module paths
   - schema enums and receipt strings
   - state dirs and config storage paths
   - `.meshpack` file format
   - repo slug

4. `preserve internal`
   - hook marker/script names
   - session/runtime details
   - historical records that describe old reality accurately

## Notes

- This inventory is intentionally narrow: it covers public or compatibility-
  sensitive surfaces, not every internal `agentmesh` string in tests.
- `.loom/` and `data/` were intentionally not staged or modified.
- The next safe rename slice is still docs-first, not code-first.
