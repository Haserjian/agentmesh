"""Ed25519 key management for witness signing."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization


def _keys_dir(data_dir: Path | None = None) -> Path:
    base = data_dir or (Path.home() / ".agentmesh")
    d = base / "keys"
    d.mkdir(parents=True, exist_ok=True)
    return d


def key_id_from_public(pub: Ed25519PublicKey) -> str:
    """Derive key ID: mesh_<sha256(pub_bytes)[:16]>."""
    raw = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    h = hashlib.sha256(raw).hexdigest()[:16]
    return f"mesh_{h}"


def generate_key(data_dir: Path | None = None) -> tuple[str, Ed25519PrivateKey]:
    """Generate a new Ed25519 keypair and save to disk. Returns (key_id, private_key)."""
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    kid = key_id_from_public(pub)

    kdir = _keys_dir(data_dir)

    # Save private key (PEM, no encryption)
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    (kdir / f"{kid}.pem").write_bytes(priv_pem)

    # Save public key (PEM)
    pub_pem = pub.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    (kdir / f"{kid}.pub").write_bytes(pub_pem)

    # Metadata
    meta = {"key_id": kid, "algorithm": "Ed25519"}
    (kdir / f"{kid}.json").write_text(json.dumps(meta, indent=2) + "\n")

    return kid, priv


def load_private_key(key_id: str, data_dir: Path | None = None) -> Ed25519PrivateKey:
    """Load a private key by key_id."""
    kdir = _keys_dir(data_dir)
    pem_path = kdir / f"{key_id}.pem"
    if not pem_path.exists():
        raise FileNotFoundError(f"Key not found: {key_id}")
    priv = serialization.load_pem_private_key(pem_path.read_bytes(), password=None)
    if not isinstance(priv, Ed25519PrivateKey):
        raise ValueError(f"Key {key_id} is not Ed25519")
    return priv


def load_public_key(key_id: str, data_dir: Path | None = None) -> Ed25519PublicKey:
    """Load a public key by key_id."""
    kdir = _keys_dir(data_dir)
    pub_path = kdir / f"{key_id}.pub"
    if not pub_path.exists():
        raise FileNotFoundError(f"Public key not found: {key_id}")
    pub = serialization.load_pem_public_key(pub_path.read_bytes())
    if not isinstance(pub, Ed25519PublicKey):
        raise ValueError(f"Key {key_id} is not Ed25519")
    return pub


def get_default_key_id(data_dir: Path | None = None) -> str | None:
    """Get the first available key ID, or None."""
    kdir = _keys_dir(data_dir)
    pems = sorted(kdir.glob("mesh_*.pem"))
    if not pems:
        return None
    return pems[0].stem


def list_keys(data_dir: Path | None = None) -> list[str]:
    """List all key IDs."""
    kdir = _keys_dir(data_dir)
    return sorted(p.stem for p in kdir.glob("mesh_*.pem"))
