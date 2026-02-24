"""Canonical Witness Envelope (cwe_v1) -- sign and verify commit witnesses."""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import __version__, episodes, gitbridge, keystore

SCHEMA_VERSION = "cwe_v1"
TRAILER_EPISODE = "AgentMesh-Episode"
TRAILER_KEYID = "AgentMesh-KeyID"
TRAILER_WITNESS = "AgentMesh-Witness"
TRAILER_SIG = "AgentMesh-Sig"
TRAILER_WITNESS_ENCODING = "AgentMesh-Witness-Encoding"
TRAILER_WITNESS_CHUNK_COUNT = "AgentMesh-Witness-Chunk-Count"
TRAILER_WITNESS_CHUNK = "AgentMesh-Witness-Chunk"

DEFAULT_PAYLOAD_ENCODING = "gzip+base64url"
DEFAULT_PAYLOAD_CHUNK_SIZE = 180


def _canonicalize(obj: dict[str, Any]) -> bytes:
    """JCS-compatible canonical JSON (RFC 8785 subset).

    Sorted keys, compact separators, UTF-8. Rewrites -0.0 to 0.0.
    """

    def _sanitize(v: Any) -> Any:
        if isinstance(v, float) and v == 0.0 and str(v) == "-0.0":
            return 0.0
        if isinstance(v, dict):
            return {k: _sanitize(val) for k, val in v.items()}
        if isinstance(v, list):
            return [_sanitize(item) for item in v]
        return v

    clean = _sanitize(obj)
    return json.dumps(clean, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _urlsafe_b64decode(data: str) -> bytes:
    """Decode URL-safe base64 with optional missing padding."""
    data = data.strip()
    if not data:
        return b""
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _compute_files_fingerprint(files: list[str]) -> tuple[int, str]:
    """Compute deterministic fingerprint for a file path list."""
    normalized = sorted(f for f in files if f)
    payload = "\0".join(normalized).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return len(normalized), f"sha256:{digest}"


def witness_hash(canonical_bytes: bytes) -> str:
    """SHA-256 hash of canonical witness bytes."""
    return f"sha256:{hashlib.sha256(canonical_bytes).hexdigest()}"


def build_witness(
    episode_id: str,
    patch_id_stable: str,
    patch_hash_verbatim: str,
    files: list[str],
    agent_id: str,
    signer_key_id: str,
    signer_public_key: str,
) -> dict[str, Any]:
    """Build the witness envelope dict (unsigned)."""
    from datetime import datetime, timezone

    files_count, files_hash = _compute_files_fingerprint(files)

    return {
        "schema_version": SCHEMA_VERSION,
        "episode_id": episode_id,
        "patch_id_stable": patch_id_stable,
        "patch_hash_verbatim": patch_hash_verbatim,
        "files_count": files_count,
        "files_hash": files_hash,
        "agent_id": agent_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "signer": {
            "algorithm": "ed25519",
            "key_id": signer_key_id,
            "public_key": signer_public_key,
        },
        "tool_versions": {
            "agentmesh": __version__,
        },
    }


def sign_witness(
    witness_dict: dict[str, Any],
    key_id: str,
    data_dir: Path | None = None,
) -> tuple[str, str, str]:
    """Sign a witness envelope. Returns (witness_hash, signature_b64, key_id)."""
    priv = keystore.load_private_key(key_id, data_dir)
    canonical = _canonicalize(witness_dict)
    sig = priv.sign(canonical)
    sig_b64 = base64.urlsafe_b64encode(sig).decode("ascii")
    w_hash = witness_hash(canonical)
    return w_hash, sig_b64, key_id


def verify_signature(
    witness_dict: dict[str, Any],
    signature_b64: str,
    key_id: str,
    data_dir: Path | None = None,
) -> bool:
    """Verify a witness signature using embedded signer key when available."""
    canonical = _canonicalize(witness_dict)

    try:
        sig = _urlsafe_b64decode(signature_b64)
    except Exception:
        return False

    signer = witness_dict.get("signer", {}) if isinstance(witness_dict.get("signer"), dict) else {}
    signer_key_id = str(signer.get("key_id", "")).strip()
    signer_pub_b64 = str(signer.get("public_key", "")).strip()

    # Preferred path: self-contained verification from embedded public key.
    if signer_pub_b64:
        try:
            pub_raw = _urlsafe_b64decode(signer_pub_b64)
            pub = Ed25519PublicKey.from_public_bytes(pub_raw)
            derived_kid = keystore.key_id_from_public_bytes(pub_raw)
            expected_kid = key_id or signer_key_id
            if expected_kid and derived_kid != expected_kid:
                return False
            pub.verify(sig, canonical)
            return True
        except Exception:
            return False

    # Backward-compatible path: verify via local keystore.
    effective_kid = key_id or signer_key_id
    if not effective_kid:
        return False
    try:
        pub = keystore.load_public_key(effective_kid, data_dir)
        pub.verify(sig, canonical)
        return True
    except (InvalidSignature, FileNotFoundError, ValueError):
        return False


def encode_witness_payload(
    witness_dict: dict[str, Any],
    encoding: str = DEFAULT_PAYLOAD_ENCODING,
    chunk_size: int = DEFAULT_PAYLOAD_CHUNK_SIZE,
) -> tuple[str, list[str]]:
    """Encode witness payload for trailer transport, split into chunks."""
    canonical = _canonicalize(witness_dict)

    if encoding == "gzip+base64url":
        payload = gzip.compress(canonical)
    elif encoding == "base64url":
        payload = canonical
    else:
        raise ValueError(f"Unsupported witness payload encoding: {encoding}")

    encoded = base64.urlsafe_b64encode(payload).decode("ascii")
    chunks = [encoded[i : i + chunk_size] for i in range(0, len(encoded), chunk_size)] if encoded else []
    return encoding, chunks


def decode_witness_payload(encoding: str, chunks: list[str]) -> dict[str, Any] | None:
    """Decode witness payload from trailer chunks."""
    if not chunks:
        return None

    try:
        raw = _urlsafe_b64decode("".join(chunks))
        if encoding == "gzip+base64url":
            data = gzip.decompress(raw)
        elif encoding == "base64url":
            data = raw
        else:
            return None

        obj = json.loads(data.decode("utf-8"))
        if not isinstance(obj, dict):
            return None
        return obj
    except Exception:
        return None


def store_witness(
    witness_dict: dict[str, Any],
    w_hash: str,
    data_dir: Path | None = None,
) -> Path:
    """Store full witness JSON to sidecar file."""
    base = data_dir or (Path.home() / ".agentmesh")
    wdir = base / "witnesses"
    wdir.mkdir(parents=True, exist_ok=True)
    hash_hex = w_hash.split(":", 1)[1] if ":" in w_hash else w_hash
    path = wdir / f"{hash_hex}.json"
    path.write_text(json.dumps(witness_dict, indent=2) + "\n")
    return path


def load_witness(w_hash: str, data_dir: Path | None = None) -> dict[str, Any] | None:
    """Load a witness from sidecar file by hash."""
    base = data_dir or (Path.home() / ".agentmesh")
    hash_hex = w_hash.split(":", 1)[1] if ":" in w_hash else w_hash
    path = base / "witnesses" / f"{hash_hex}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def build_trailers(
    episode_id: str,
    key_id: str,
    w_hash: str,
    sig_b64: str,
    witness_dict: dict[str, Any],
) -> str:
    """Build multi-line trailer string for git commit."""
    encoding, chunks = encode_witness_payload(witness_dict)
    lines = [
        f"{TRAILER_EPISODE}: {episode_id}",
        f"{TRAILER_KEYID}: {key_id}",
        f"{TRAILER_WITNESS}: {w_hash}",
        f"{TRAILER_SIG}: {sig_b64}",
        f"{TRAILER_WITNESS_ENCODING}: {encoding}",
        f"{TRAILER_WITNESS_CHUNK_COUNT}: {len(chunks)}",
    ]
    for chunk in chunks:
        lines.append(f"{TRAILER_WITNESS_CHUNK}: {chunk}")
    return "\n".join(lines)


def parse_trailers(message: str) -> dict[str, Any]:
    """Extract AgentMesh trailers from a commit message."""
    scalar_keys = {
        TRAILER_EPISODE,
        TRAILER_KEYID,
        TRAILER_WITNESS,
        TRAILER_SIG,
        TRAILER_WITNESS_ENCODING,
        TRAILER_WITNESS_CHUNK_COUNT,
    }
    multi_keys = {TRAILER_WITNESS_CHUNK}

    result: dict[str, Any] = {}
    for line in message.splitlines():
        line = line.strip()
        for key in scalar_keys | multi_keys:
            prefix = f"{key}: "
            if line.startswith(prefix):
                val = line[len(prefix) :].strip()
                if key in multi_keys:
                    result.setdefault(key, []).append(val)
                else:
                    result[key] = val
    return result


def create_and_sign(
    agent_id: str,
    cwd: str | None = None,
    data_dir: Path | None = None,
) -> tuple[dict[str, Any], str, str, str, str] | None:
    """Build, sign, persist, and encode witness. Returns trailers bundle or None."""
    ep_id = episodes.get_current_episode(data_dir)
    if not ep_id:
        return None

    kid = keystore.get_default_key_id(data_dir)
    if not kid:
        return None

    diff_text = gitbridge.get_staged_diff(cwd)
    if not diff_text:
        return None

    patch_id = gitbridge.compute_patch_id_stable(diff_text, cwd)
    patch_hash = gitbridge.compute_patch_hash(diff_text)
    files = gitbridge.get_staged_files(cwd)
    signer_pub_b64 = keystore.public_key_b64(kid, data_dir)

    w = build_witness(
        episode_id=ep_id,
        patch_id_stable=patch_id or "",
        patch_hash_verbatim=patch_hash,
        files=files,
        agent_id=agent_id,
        signer_key_id=kid,
        signer_public_key=signer_pub_b64,
    )

    w_hash, sig_b64, kid = sign_witness(w, kid, data_dir)
    store_witness(w, w_hash, data_dir)

    trailer = build_trailers(ep_id, kid, w_hash, sig_b64, w)
    return w, w_hash, sig_b64, kid, trailer


class VerifyResult:
    """Result of witness verification."""

    def __init__(self, status: str, details: str = ""):
        self.status = status
        self.details = details

    @property
    def ok(self) -> bool:
        return self.status == "VERIFIED"

    def __repr__(self) -> str:
        return f"VerifyResult({self.status!r}, {self.details!r})"


def verify_commit(
    commit_sha: str,
    cwd: str | None = None,
    data_dir: Path | None = None,
) -> VerifyResult:
    """Verify a commit's witness envelope."""
    msg = gitbridge._run_git(["log", "-1", "--format=%B", commit_sha], cwd=cwd)
    if not msg:
        return VerifyResult("NO_TRAILERS", "Could not read commit message")

    trailers = parse_trailers(msg)
    if TRAILER_SIG not in trailers or TRAILER_WITNESS not in trailers:
        return VerifyResult("NO_TRAILERS", "No witness trailers found")

    kid = str(trailers.get(TRAILER_KEYID, ""))
    sig_b64 = str(trailers[TRAILER_SIG])
    w_hash = str(trailers[TRAILER_WITNESS])

    # Preferred source: portable payload in commit trailers.
    w: dict[str, Any] | None = None
    payload_encoding = str(trailers.get(TRAILER_WITNESS_ENCODING, ""))
    payload_chunks = trailers.get(TRAILER_WITNESS_CHUNK, [])
    if isinstance(payload_chunks, list) and payload_chunks and payload_encoding:
        chunk_count_raw = str(trailers.get(TRAILER_WITNESS_CHUNK_COUNT, "")).strip()
        try:
            chunk_count = int(chunk_count_raw) if chunk_count_raw else len(payload_chunks)
        except ValueError:
            chunk_count = len(payload_chunks)
        w = decode_witness_payload(payload_encoding, payload_chunks[:chunk_count])
        if w is None:
            return VerifyResult("WITNESS_PAYLOAD_INVALID", "Could not decode witness payload from trailers")

        inline_hash = witness_hash(_canonicalize(w))
        if inline_hash != w_hash:
            return VerifyResult(
                "WITNESS_HASH_MISMATCH",
                f"trailer={w_hash}, payload={inline_hash}",
            )

    # Backward-compatible source: local sidecar by witness hash.
    if w is None:
        w = load_witness(w_hash, data_dir)
    if w is None:
        return VerifyResult("WITNESS_MISSING", f"Witness {w_hash} not found in trailers or sidecar")

    if not verify_signature(w, sig_b64, kid, data_dir):
        return VerifyResult("SIGNATURE_INVALID", "Ed25519 signature verification failed")

    diff_text = gitbridge._run_git(["show", "--format=", "--patch", commit_sha], cwd=cwd)
    if diff_text:
        recomputed = gitbridge.compute_patch_id_stable(diff_text, cwd)
        witness_patch_id = str(w.get("patch_id_stable", ""))
        if recomputed and witness_patch_id and recomputed != witness_patch_id:
            return VerifyResult(
                "PATCH_MISMATCH",
                f"patch_id_stable mismatch: witness={witness_patch_id}, recomputed={recomputed}",
            )

    changed_files = gitbridge.get_commit_files(commit_sha, cwd)
    files_count, files_hash = _compute_files_fingerprint(changed_files)

    if "files_count" in w:
        try:
            witness_count = int(w.get("files_count"))
            if witness_count != files_count:
                return VerifyResult(
                    "FILES_MISMATCH",
                    f"files_count mismatch: witness={witness_count}, recomputed={files_count}",
                )
        except (TypeError, ValueError):
            return VerifyResult("FILES_MISMATCH", "files_count in witness is invalid")

    if "files_hash" in w:
        witness_hash_value = str(w.get("files_hash", ""))
        if witness_hash_value and witness_hash_value != files_hash:
            return VerifyResult(
                "FILES_MISMATCH",
                f"files_hash mismatch: witness={witness_hash_value}, recomputed={files_hash}",
            )

    return VerifyResult("VERIFIED", f"Signed by {kid or 'embedded signer'}")
