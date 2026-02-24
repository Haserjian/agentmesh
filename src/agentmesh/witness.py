"""Canonical Witness Envelope (cwe_v1) -- sign and verify commit witnesses."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature

from . import __version__, episodes, gitbridge, keystore

SCHEMA_VERSION = "cwe_v1"
TRAILER_EPISODE = "AgentMesh-Episode"
TRAILER_KEYID = "AgentMesh-KeyID"
TRAILER_WITNESS = "AgentMesh-Witness"
TRAILER_SIG = "AgentMesh-Sig"


def _canonicalize(obj: dict[str, Any]) -> bytes:
    """JCS-compatible canonical JSON (RFC 8785 subset).

    Sorted keys, compact separators, UTF-8. Rejects -0.
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


def witness_hash(canonical_bytes: bytes) -> str:
    """SHA-256 hash of canonical witness bytes."""
    return f"sha256:{hashlib.sha256(canonical_bytes).hexdigest()}"


def build_witness(
    episode_id: str,
    patch_id_stable: str,
    patch_hash_verbatim: str,
    files: list[str],
    agent_id: str,
) -> dict[str, Any]:
    """Build the witness envelope dict (unsigned)."""
    from datetime import datetime, timezone

    return {
        "schema_version": SCHEMA_VERSION,
        "episode_id": episode_id,
        "patch_id_stable": patch_id_stable,
        "patch_hash_verbatim": patch_hash_verbatim,
        "files": sorted(files),
        "agent_id": agent_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
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
    """Verify a witness signature. Returns True if valid."""
    pub = keystore.load_public_key(key_id, data_dir)
    canonical = _canonicalize(witness_dict)
    sig = base64.urlsafe_b64decode(signature_b64)
    try:
        pub.verify(sig, canonical)
        return True
    except InvalidSignature:
        return False


def store_witness(
    witness_dict: dict[str, Any],
    w_hash: str,
    data_dir: Path | None = None,
) -> Path:
    """Store full witness JSON to sidecar file."""
    base = data_dir or (Path.home() / ".agentmesh")
    wdir = base / "witnesses"
    wdir.mkdir(parents=True, exist_ok=True)
    # Use hash without prefix for filename
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
) -> str:
    """Build the multi-line trailer string for git commit."""
    lines = [
        f"{TRAILER_EPISODE}: {episode_id}",
        f"{TRAILER_KEYID}: {key_id}",
        f"{TRAILER_WITNESS}: {w_hash}",
        f"{TRAILER_SIG}: {sig_b64}",
    ]
    return "\n".join(lines)


def parse_trailers(message: str) -> dict[str, str]:
    """Extract AgentMesh trailers from a commit message."""
    result: dict[str, str] = {}
    for line in message.splitlines():
        line = line.strip()
        for key in (TRAILER_EPISODE, TRAILER_KEYID, TRAILER_WITNESS, TRAILER_SIG):
            prefix = f"{key}: "
            if line.startswith(prefix):
                result[key] = line[len(prefix):].strip()
    return result


def create_and_sign(
    agent_id: str,
    cwd: str | None = None,
    data_dir: Path | None = None,
) -> tuple[dict[str, Any], str, str, str, str] | None:
    """Full witness creation: build, sign, store.

    Returns (witness_dict, w_hash, sig_b64, key_id, trailer_string) or None if no key.
    """
    ep_id = episodes.get_current_episode(data_dir)
    if not ep_id:
        return None

    kid = keystore.get_default_key_id(data_dir)
    if not kid:
        return None

    # Compute diff identities from staged changes
    diff_text = gitbridge.get_staged_diff(cwd)
    if not diff_text:
        return None

    patch_id = gitbridge.compute_patch_id_stable(diff_text, cwd)
    patch_hash = gitbridge.compute_patch_hash(diff_text)
    files = gitbridge.get_staged_files(cwd)

    w = build_witness(
        episode_id=ep_id,
        patch_id_stable=patch_id or "",
        patch_hash_verbatim=patch_hash,
        files=files,
        agent_id=agent_id,
    )

    w_hash, sig_b64, kid = sign_witness(w, kid, data_dir)
    store_witness(w, w_hash, data_dir)

    trailer = build_trailers(ep_id, kid, w_hash, sig_b64)
    return w, w_hash, sig_b64, kid, trailer


class VerifyResult:
    """Result of witness verification."""

    def __init__(self, status: str, details: str = ""):
        self.status = status  # VERIFIED, SIGNATURE_INVALID, PATCH_MISMATCH, WITNESS_MISSING, NO_TRAILERS
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
    # Get commit message
    msg = gitbridge._run_git(["log", "-1", "--format=%B", commit_sha], cwd=cwd)
    if not msg:
        return VerifyResult("NO_TRAILERS", "Could not read commit message")

    trailers = parse_trailers(msg)
    if TRAILER_SIG not in trailers or TRAILER_WITNESS not in trailers:
        return VerifyResult("NO_TRAILERS", "No witness trailers found")

    kid = trailers.get(TRAILER_KEYID, "")
    sig_b64 = trailers[TRAILER_SIG]
    w_hash = trailers[TRAILER_WITNESS]

    # Load witness from sidecar
    w = load_witness(w_hash, data_dir)
    if w is None:
        return VerifyResult("WITNESS_MISSING", f"Witness {w_hash} not found in sidecar store")

    # Verify signature
    try:
        valid = verify_signature(w, sig_b64, kid, data_dir)
    except (FileNotFoundError, ValueError) as e:
        return VerifyResult("SIGNATURE_INVALID", f"Key error: {e}")

    if not valid:
        return VerifyResult("SIGNATURE_INVALID", "Ed25519 signature verification failed")

    # Verify patch_id_stable against commit diff
    diff_text = gitbridge._run_git(["show", "--format=", "--patch", commit_sha], cwd=cwd)
    if diff_text:
        recomputed = gitbridge.compute_patch_id_stable(diff_text, cwd)
        if recomputed and w.get("patch_id_stable") and recomputed != w["patch_id_stable"]:
            return VerifyResult(
                "PATCH_MISMATCH",
                f"patch_id_stable mismatch: witness={w['patch_id_stable']}, recomputed={recomputed}",
            )

    return VerifyResult("VERIFIED", f"Signed by {kid}")
