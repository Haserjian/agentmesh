# Spec: Meshpack Authentic Signing (v0.4)

Status: **SPEC ONLY** -- implementation parked behind v0.3 product work.

## Problem

v0.3 meshpacks use HMAC-SHA256 keyed by `episode_id`, which is embedded in the manifest. Any party who can read a meshpack can re-sign a modified copy. This is fine for local-trusted workflows (same machine, same team) but breaks down when packs cross trust boundaries (handoff to external auditor, import from untrusted source).

## Threat Model Split

### Local-Trusted (v0.3, current)

- Producer and consumer share the same machine or trusted network.
- Tampering is detectable (HMAC catches accidental corruption).
- Forgery is trivial (key = episode_id, visible in manifest).
- **Acceptable for**: dev handoffs, same-team context sharing, local backup/restore.

### Cross-Boundary (v0.4, this spec)

- Producer and consumer do NOT share implicit trust.
- Pack may traverse untrusted storage (S3, email, shared drive).
- **Requirements**:
  - Authenticity: verify WHO signed the pack.
  - Integrity: detect ANY modification to payload or manifest.
  - Non-repudiation: signer cannot deny producing the pack.
  - Fail-closed: missing or invalid signature = reject.

## Design

### Key Source

Use the existing AgentMesh keystore (`agentmesh key generate/info/export/import`). The signing key is an Ed25519 private key managed by `AssayKeyStore`-compatible logic (or a dedicated `MeshKeyStore` if decoupled).

- Signer specifies key by ID: `--signer <key-id>`
- If no `--signer` flag, fall back to the default key (first active key in keystore).
- If no keys exist, `export` fails with actionable error: "No signing key. Run `agentmesh key generate` first."

### Signature Format

Detached signature over `canonical_payload`:

```
canonical_payload = SHA-256(capsules.jsonl) ||
                    SHA-256(claims_snapshot.jsonl) ||
                    SHA-256(messages.jsonl) ||
                    SHA-256(weave_slice.jsonl) ||
                    SHA-256(manifest_without_signature_fields)
```

Manifest gains two fields:

```json
{
  "signer_key_id": "mesh_abc123...",
  "signature": "<base64-encoded Ed25519 signature over canonical_payload>",
  "signature_version": 1
}
```

The legacy `"signature"` field (HMAC) is renamed to `"hmac_legacy"` for backward compat detection.

### Tar Layout (unchanged)

```
manifest.json
capsules.jsonl
claims_snapshot.jsonl
messages.jsonl
weave_slice.jsonl
```

No separate `.sig` file -- signature lives in manifest for single-file portability.

### Verification Contract

**Fail-closed by default.** `verify` and `import` reject packs that:

1. Have no `signature_version` field (legacy HMAC pack).
2. Have `signature_version: 1` but missing `signer_key_id` or `signature`.
3. Have a valid signature format but the signature does not verify against the claimed key.
4. Reference a `signer_key_id` not present in the local keystore (unless public key provided inline via `--signer-pubkey`).

Exit codes:
- 0: valid, authentic
- 1: signature invalid or verification failed
- 2: legacy pack detected (no `signature_version`), rejected unless `--legacy-accept`

### Migration / Back-Compat

- v0.3 packs lack `signature_version`. They are detected as legacy.
- `agentmesh episode verify <pack>` on a legacy pack: exit 2, message: "Legacy HMAC pack. Use --legacy-accept to verify with episode_id key."
- `agentmesh episode import <pack>` on a legacy pack: reject by default. `--legacy-accept` flag required.
- `agentmesh episode verify --legacy-accept <pack>`: falls back to v0.3 HMAC verification.
- No automatic upgrade path (re-export with `--signer` to produce a v1-signed pack).

## CLI / API

### Export

```
agentmesh episode export <episode-id> [--out <path>] [--signer <key-id>]
```

- `--signer`: key ID from keystore. If omitted, uses default key. If no keys, error.
- Output: `.meshpack` with Ed25519 signature in manifest.

### Verify

```
agentmesh episode verify <pack-path> [--require-auth] [--legacy-accept] [--signer-pubkey <hex>]
```

- `--require-auth`: default `true` in v0.4. Rejects unsigned/legacy packs.
- `--legacy-accept`: allow v0.3 HMAC verification (explicit opt-in only).
- `--signer-pubkey`: verify against a specific public key (for packs from unknown keystores).

### Import

```
agentmesh episode import <pack-path> [--namespace <ns>] [--legacy-accept]
```

- Runs `verify` internally before import. Fails if verification fails.
- `--legacy-accept`: allow importing v0.3 HMAC packs.

### Python API

```python
from agentmesh.passport import export_episode, verify_meshpack, import_meshpack

# v0.4 signatures
export_episode(episode_id, output_path, signer_key_id="mesh_abc", data_dir=...)
valid, manifest = verify_meshpack(pack_path, require_auth=True)
counts = import_meshpack(pack_path, namespace="ext", require_auth=True, data_dir=...)

# Legacy fallback
valid, manifest = verify_meshpack(pack_path, legacy_accept=True)
```

## Acceptance Tests

These define the contract. Implementation must pass all before merge.

### 1. Tampered payload fails

```python
def test_tampered_payload_rejects():
    # Export with signer key
    # Modify capsules.jsonl inside tar
    # verify_meshpack(tampered, require_auth=True) -> (False, ...)
```

### 2. Wrong signer fails

```python
def test_wrong_signer_rejects():
    # Export with key A
    # Verify expecting key B (via --signer-pubkey)
    # verify_meshpack(pack, signer_pubkey=key_b_pub) -> (False, ...)
```

### 3. Missing signature fails

```python
def test_missing_signature_rejects():
    # Build a pack with no signature fields in manifest
    # verify_meshpack(pack, require_auth=True) -> (False, ...)
```

### 4. Legacy pack rejected by default

```python
def test_legacy_pack_rejected_default():
    # Export using v0.3 HMAC (no signature_version)
    # verify_meshpack(pack, require_auth=True) -> (False, ...)
    # import_meshpack(pack, require_auth=True) -> raises ValueError
```

### 5. Legacy pack accepted with flag

```python
def test_legacy_pack_accepted_with_flag():
    # Export using v0.3 HMAC
    # verify_meshpack(pack, legacy_accept=True) -> (True, ...)
    # import_meshpack(pack, legacy_accept=True) -> succeeds
```

### 6. Valid signed pack round-trips

```python
def test_signed_pack_roundtrip():
    # Generate key, export with signer, verify, import into fresh DB
    # All succeed, counts match
```

## Non-Goals (v0.4)

- Key rotation / multi-signer (v0.5+)
- Encryption at rest (out of scope -- packs are evidence, not secrets)
- Remote key resolution (v0.5+ with keyserver)
- Timestamping / notarization (future trust tier work)

## Dependencies

- Ed25519 from `cryptography` or stdlib `hashlib` + `nacl` -- TBD, prefer zero-new-dep if possible (Python 3.12+ has no stdlib Ed25519; will likely need `cryptography` or vendored `ed25519`).
- Keystore module (new or borrowed from Assay pattern).
