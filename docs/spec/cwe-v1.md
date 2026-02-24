# Canonical Witness Envelope v1 (cwe_v1)

## Purpose

Turn every `agentmesh commit` into a signed witness envelope that CI can verify
without external services. Upgrades commit trailers from attribution metadata
into cryptographic proof that intent, diff, and episode lineage are consistent.

## Witness Schema

```json
{
  "schema_version": "cwe_v1",
  "episode_id": "ep_...",
  "patch_id_stable": "<hex from git patch-id --stable>",
  "patch_hash_verbatim": "sha256:<hex of git diff --cached bytes>",
  "files_count": 2,
  "files_hash": "sha256:<hex of sorted file list joined with NUL separators>",
  "agent_id": "claude_ttys001",
  "timestamp": "2026-02-24T08:30:00+00:00",
  "signer": {
    "algorithm": "ed25519",
    "key_id": "mesh_a1b2c3d4e5f6g7h8",
    "public_key": "<base64url raw 32-byte Ed25519 pubkey>"
  },
  "tool_versions": {
    "agentmesh": "0.5.2"
  }
}
```

### Field semantics

| Field | Required | Signed | Survives rebase |
|-------|----------|--------|-----------------|
| `schema_version` | yes | yes | yes |
| `episode_id` | yes | yes | yes |
| `patch_id_stable` | yes | yes | **yes** (primary diff identity) |
| `patch_hash_verbatim` | yes | yes | no (exact bytes) |
| `files_count` | yes | yes | yes |
| `files_hash` | yes | yes | yes |
| `agent_id` | yes | yes | yes |
| `timestamp` | yes | yes | yes |
| `signer` | yes | yes | yes |
| `tool_versions` | yes | yes | yes |

**`commit_sha` is NOT in the signed preimage.** It changes on rebase/cherry-pick
and would produce false "tampered" signals. It's stored as unsigned metadata
in the trailer only.

### Diff identity

Two identities for defense in depth:

1. **`patch_id_stable`**: output of `git diff --cached | git patch-id --stable`.
   Survives rebase, cherry-pick, and context-line changes. Primary verification target.

2. **`patch_hash_verbatim`**: SHA-256 of raw `git diff --cached` bytes.
   Exact-match "courtroom mode." Breaks on rebase but proves bit-identical diff.

## Canonicalization

RFC 8785 (JCS) compatible:
- Keys sorted lexicographically
- Compact separators (`,` and `:`)
- No trailing newline
- Reject `-0` (serialize as `0`)
- UTF-8 encoding

```python
canonical = json.dumps(witness, sort_keys=True, separators=(",", ":"))
```

## Cryptographic operations

- **Algorithm**: Ed25519 (RFC 8032)
- **Preimage**: canonical JSON bytes (UTF-8)
- **Signature**: Ed25519 sign(private_key, canonical_bytes) -> 64 bytes
- **Key ID**: `mesh_<sha256(public_key_bytes)[:16]>`
- **Witness hash**: `sha256:<sha256(canonical_bytes)>`

## Git trailers

```
AgentMesh-Episode: ep_abc123
AgentMesh-KeyID: mesh_a1b2c3d4e5f6g7h8
AgentMesh-Witness: sha256:abc123...
AgentMesh-Sig: <base64url of 64-byte Ed25519 signature>
AgentMesh-Witness-Encoding: gzip+base64url
AgentMesh-Witness-Chunk-Count: 3
AgentMesh-Witness-Chunk: <chunk1>
AgentMesh-Witness-Chunk: <chunk2>
AgentMesh-Witness-Chunk: <chunk3>
```

All trailers are injected atomically by `agentmesh commit`. The witness payload
is portable via chunked trailer encoding; sidecar storage is retained for local
inspection/backward compatibility.

## Storage

Full witness JSON is stored in `.agentmesh/witnesses/<witness_hash>.json`.
Portable payload lives in commit trailers (`AgentMesh-Witness-Chunk`), so CI
can verify without local AgentMesh state.

## Verification algorithm

1. Extract trailers from commit message
2. Decode inline witness payload from `AgentMesh-Witness-Chunk` trailers
3. Recompute witness hash and compare to `AgentMesh-Witness` trailer
4. Fallback (legacy): load witness from sidecar by `AgentMesh-Witness` hash when inline payload is absent
5. Verify Ed25519 signature over canonical witness JSON using embedded signer public key
6. Ensure embedded signer key ID matches `AgentMesh-KeyID`
7. Recompute `patch_id_stable` from commit diff and compare to witness
8. Recompute file fingerprint and compare to `files_count` + `files_hash`
9. Report: VERIFIED / SIGNATURE_INVALID / PATCH_MISMATCH / FILES_MISMATCH / WITNESS_MISSING

## Enforcement gradient

1. **Advisory** (default): log result, never fail
2. **Warn**: exit 0 but emit warnings for missing/invalid witnesses
3. **Strict**: exit 1 on any missing or invalid witness
4. **Forensic**: strict + require `patch_hash_verbatim` match (no rebase tolerance)

## Squash merge handling

A squash commit that combines N witnessed commits should carry:
- Its own witness (new patch_id for the squashed diff)
- Optional `constituent_witnesses` array of hashes (for audit trail)

## Future extensions

- SSH signing key support (reuse developers' existing keys)
- `allowed_signers` file for team key distribution
- Trust Thermostat (auto-adjust enforcement by path/team)
