"""AgentMesh -> Assay proof-pack adapter.

This module deliberately delegates the proof-pack kernel to Assay. AgentMesh
only projects episode, output, and tool-decision facts into Assay receipts.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from . import __version__, db, events, gitbridge
from .models import Event, EventKind, _now


DEFAULT_OUTPUT_EXCLUDES = frozenset(
    {
        ".DS_Store",
        "__pycache__",
        "*.pyc",
        "*.pyo",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)


@dataclass(frozen=True)
class AgentMeshAssayPackResult:
    pack_dir: Path
    run_id: str
    receipt_count: int
    output_root_digest: str
    sidecar_dir: Path


def _load_assay_kernel():
    try:
        from assay._receipts.jcs import canonicalize as jcs_canonicalize
        from assay.proof_pack import ProofPack
    except ImportError as exc:
        raise RuntimeError(
            "Assay proof-pack support requires assay-ai. "
            "Install agentmesh-core[assay] or install assay-ai in this environment."
        ) from exc
    return ProofPack, jcs_canonicalize


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_prefixed(data: bytes) -> str:
    return "sha256:" + _sha256_hex(data)


def _jcs_digest(obj: Any) -> str:
    _, jcs_canonicalize = _load_assay_kernel()
    return _sha256_prefixed(jcs_canonicalize(obj))


def _should_exclude(rel: str, patterns: Iterable[str]) -> bool:
    import fnmatch

    parts = rel.split("/")
    for pattern in patterns:
        if fnmatch.fnmatch(rel, pattern):
            return True
        if any(fnmatch.fnmatch(part, pattern) for part in parts):
            return True
    return False


def build_outputs_manifest(
    outputs_dir: Path | str,
    *,
    excludes: Iterable[str] = DEFAULT_OUTPUT_EXCLUDES,
) -> dict[str, Any]:
    """Build a stable output manifest using Assay JCS for the root digest."""
    root = Path(outputs_dir)
    if not root.exists():
        raise FileNotFoundError(f"outputs directory does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"outputs path is not a directory: {root}")

    root_resolved = root.resolve()
    exclude_set = sorted(set(excludes))
    files: list[dict[str, Any]] = []
    for path in sorted(root_resolved.rglob("*"), key=lambda p: p.relative_to(root_resolved).as_posix()):
        if not path.is_file():
            continue
        rel = path.relative_to(root_resolved).as_posix()
        if _should_exclude(rel, exclude_set):
            continue
        data = path.read_bytes()
        files.append(
            {
                "path": rel,
                "sha256": _sha256_hex(data),
                "bytes": len(data),
            }
        )

    body = {
        "schema": "agentmesh.outputs_manifest/v1",
        "hash_alg": "sha256",
        "canon": "assay.jcs-rfc8785",
        "excludes": exclude_set,
        "files": files,
        "file_count": len(files),
        "total_bytes": sum(int(f["bytes"]) for f in files),
    }
    return {**body, "root_digest": _jcs_digest(body)}


def _file_manifest(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "path": path.name,
        "sha256": _sha256_hex(data),
        "bytes": len(data),
    }


def _event_episode_id(event: Event) -> str:
    payload = event.payload or {}
    return str(
        payload.get("_ewp_episode_id")
        or payload.get("episode_id")
        or payload.get("episode")
        or ""
    )


def _episode_events(episode_id: str, data_dir: Path | None) -> list[Event]:
    return [event for event in events.read_events(data_dir) if _event_episode_id(event) == episode_id]


def _latest_commit_sha(repo_path: Path | None, episode_id: str, data_dir: Path | None) -> str:
    weave_events = db.list_weave_events(data_dir=data_dir, episode_id=episode_id)
    for weave in reversed(weave_events):
        if weave.git_commit_sha:
            return weave.git_commit_sha
    if repo_path and repo_path.exists():
        sha = gitbridge._run_git(["rev-parse", "HEAD"], cwd=str(repo_path))
        if len(sha) == 40:
            return sha
    return ""


def _receipt_base(
    *,
    receipt_id: str,
    receipt_type: str,
    run_id: str,
    seq: int,
    timestamp: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "receipt_id": receipt_id,
        "type": receipt_type,
        "timestamp": timestamp,
        "schema_version": "1.0",
        "run_id": run_id,
        "_trace_id": run_id,
        "seq": seq,
        "payload": payload,
    }


def _safe_assay_receipt_payload(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "sha",
        "task_id",
        "terminal_state",
        "bridge_status",
        "degraded_reason",
        "command_id",
        "tool_id",
        "ok",
        "returncode",
        "guardian_decision",
        "decision_reason",
        "argv_digest",
        "argv_redacted",
        "policy_version",
        "_ewp_version",
        "_ewp_origin",
        "_ewp_episode_id",
        "_ewp_agent_id",
    }
    safe = {key: payload[key] for key in sorted(allowed) if key in payload}
    if "guardian_decision_receipt" in payload:
        safe["guardian_decision_receipt"] = payload["guardian_decision_receipt"]
    if "gate_report" in payload:
        report = payload["gate_report"]
        if isinstance(report, dict):
            safe["gate_report"] = {
                key: report[key]
                for key in sorted(("result", "current_score", "current_grade", "regression_detected"))
                if key in report
            }
    return safe


def build_agentmesh_receipts(
    episode_id: str,
    *,
    data_dir: Path | None = None,
    repo_path: Path | None = None,
    outputs_manifest: dict[str, Any] | None = None,
    results_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Project AgentMesh episode state into proof-pack-admissible receipts."""
    episode = db.get_episode(episode_id, data_dir=data_dir)
    if episode is None:
        raise ValueError(f"episode not found: {episode_id}")

    commit_sha = _latest_commit_sha(repo_path, episode_id, data_dir)
    receipts: list[dict[str, Any]] = []

    receipts.append(
        _receipt_base(
            receipt_id=f"agentmesh_episode_{episode_id}",
            receipt_type="agentmesh.episode/v1",
            run_id=episode_id,
            seq=0,
            timestamp=episode.started_at,
            payload={
                "episode_id": episode.episode_id,
                "title": episode.title,
                "started_at": episode.started_at,
                "ended_at": episode.ended_at,
                "parent_episode_id": episode.parent_episode_id,
                "commit_sha": commit_sha,
                "repo_path_present": bool(repo_path),
                "agentmesh_version": __version__,
            },
        )
    )

    seq = 1
    if outputs_manifest is not None:
        receipts.append(
            _receipt_base(
                receipt_id=f"agentmesh_outputs_{episode_id}",
                receipt_type="agentmesh.output_manifest/v1",
                run_id=episode_id,
                seq=seq,
                timestamp=_now(),
                payload={"outputs_manifest": outputs_manifest},
            )
        )
        seq += 1

    if results_path is not None:
        result_file = Path(results_path)
        if result_file.exists() and result_file.is_file():
            receipts.append(
                _receipt_base(
                    receipt_id=f"agentmesh_results_{episode_id}",
                    receipt_type="agentmesh.result_artifact/v1",
                    run_id=episode_id,
                    seq=seq,
                    timestamp=_now(),
                    payload={"result_artifact": _file_manifest(result_file)},
                )
            )
            seq += 1

    for weave in db.list_weave_events(data_dir=data_dir, episode_id=episode_id):
        receipts.append(
            _receipt_base(
                receipt_id=f"agentmesh_weave_{weave.event_id}",
                receipt_type="agentmesh.weave_event/v1",
                run_id=episode_id,
                seq=seq,
                timestamp=weave.created_at,
                payload=weave.model_dump(),
            )
        )
        seq += 1

    for event in _episode_events(episode_id, data_dir):
        payload = event.payload or {}
        if event.kind == EventKind.ASSAY_RECEIPT and "guardian_decision_receipt" in payload:
            receipt_type = "agentmesh.guardian_decision/v1"
        elif event.kind == EventKind.ASSAY_RECEIPT:
            receipt_type = "agentmesh.assay_receipt/v1"
        else:
            continue
        receipts.append(
            _receipt_base(
                receipt_id=f"agentmesh_event_{event.event_id}",
                receipt_type=receipt_type,
                run_id=episode_id,
                seq=seq,
                timestamp=event.ts,
                payload={
                    "event_id": event.event_id,
                    "event_hash": event.event_hash,
                    "event_kind": event.kind.value if isinstance(event.kind, EventKind) else str(event.kind),
                    "agent_id": event.agent_id,
                    "assay_receipt": _safe_assay_receipt_payload(payload),
                },
            )
        )
        seq += 1

    return receipts


def _write_text(path: Path, text: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | 0o111)


def _write_sidecars(
    pack_dir: Path,
    *,
    outputs_manifest: dict[str, Any] | None,
    results_path: Path | None,
    episode_script: Path | None,
) -> Path:
    sidecar_dir = pack_dir / "_unsigned" / "agentmesh"
    sidecar_dir.mkdir(parents=True, exist_ok=True)

    if outputs_manifest is not None:
        _write_text(
            sidecar_dir / "outputs_manifest.json",
            json.dumps(outputs_manifest, indent=2, sort_keys=True) + "\n",
        )

    if results_path is not None and results_path.exists() and results_path.is_file():
        shutil.copy2(results_path, sidecar_dir / results_path.name)

    if episode_script is not None and episode_script.exists() and episode_script.is_file():
        replay_dir = sidecar_dir / "replay"
        replay_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(episode_script, replay_dir / episode_script.name)

    environment = {
        "schema": "agentmesh.environment_lock/v1",
        "agentmesh_version": __version__,
        "python": sys.version,
        "platform": platform.platform(),
        "cwd": os.getcwd(),
    }
    _write_text(
        sidecar_dir / "environment.lock.json",
        json.dumps(environment, indent=2, sort_keys=True) + "\n",
    )

    replay = """#!/usr/bin/env bash
set -euo pipefail

if [[ $# -gt 0 ]]; then
  PACK_DIR="$(cd "$1" && pwd)"
else
  PACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi
OUTPUTS_DIR="${2:-}"
WORKDIR=""

if [[ -z "$OUTPUTS_DIR" ]]; then
  EPISODE_SCRIPT="$PACK_DIR/_unsigned/agentmesh/replay/episode.sh"
  if [[ -f "$EPISODE_SCRIPT" ]]; then
    WORKDIR="$(mktemp -d)"
    trap 'rm -rf "$WORKDIR"' EXIT
    (
      cd "$WORKDIR"
      AGENTMESH_OUTPUTS_DIR="$WORKDIR/outputs" \
      AGENTMESH_RESULTS_PATH="$WORKDIR/results.xml" \
      bash "$EPISODE_SCRIPT"
    )
    OUTPUTS_DIR="$WORKDIR/outputs"
  else
    OUTPUTS_DIR="outputs"
  fi
fi

python3 - "$PACK_DIR" "$OUTPUTS_DIR" <<'PY'
import fnmatch
import hashlib
import json
import sys
from pathlib import Path

try:
    from assay._receipts.jcs import canonicalize as jcs_canonicalize
except ImportError:
    print("valid:false")
    print("assay-ai is required to recompute the signed output manifest digest", file=sys.stderr)
    sys.exit(2)

pack = Path(sys.argv[1])
outputs = Path(sys.argv[2])

expected = None
receipt_pack = pack / "receipt_pack.jsonl"
for line in receipt_pack.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    receipt = json.loads(line)
    if receipt.get("type") == "agentmesh.output_manifest/v1":
        expected = receipt.get("payload", {}).get("outputs_manifest")
        break

if not isinstance(expected, dict):
    print("valid:false")
    print("signed agentmesh.output_manifest/v1 receipt not found", file=sys.stderr)
    sys.exit(1)

files = []
excludes = expected.get("excludes", [])
for path in sorted(outputs.resolve().rglob("*"), key=lambda p: p.relative_to(outputs.resolve()).as_posix()):
    if not path.is_file():
        continue
    rel = path.relative_to(outputs.resolve()).as_posix()
    parts = rel.split("/")
    if any(fnmatch.fnmatch(rel, pat) or any(fnmatch.fnmatch(part, pat) for part in parts) for pat in excludes):
        continue
    data = path.read_bytes()
    files.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})

actual = {
    "schema": expected.get("schema"),
    "hash_alg": expected.get("hash_alg"),
    "canon": expected.get("canon"),
    "excludes": excludes,
    "files": files,
    "file_count": len(files),
    "total_bytes": sum(int(item["bytes"]) for item in files),
}
actual_root = "sha256:" + hashlib.sha256(jcs_canonicalize(actual)).hexdigest()

if actual_root != expected.get("root_digest"):
    print("valid:false")
    print(
        f"output manifest root mismatch: expected={expected.get('root_digest')} actual={actual_root}",
        file=sys.stderr,
    )
    sys.exit(1)
print("valid:true")
PY
"""
    _write_text(sidecar_dir / "replay-from-proof.sh", replay, executable=True)
    return sidecar_dir


def build_agentmesh_assay_pack(
    episode_id: str,
    output_dir: Path,
    *,
    data_dir: Path | None = None,
    repo_path: Path | None = None,
    outputs_dir: Path | None = None,
    results_path: Path | None = None,
    episode_script: Path | None = None,
    mode: str = "shadow",
    keystore: Any = None,
) -> AgentMeshAssayPackResult:
    """Build an Assay proof pack for an AgentMesh episode."""
    ProofPack, _ = _load_assay_kernel()
    outputs_manifest = (
        build_outputs_manifest(outputs_dir)
        if outputs_dir is not None
        else None
    )
    receipts = build_agentmesh_receipts(
        episode_id,
        data_dir=data_dir,
        repo_path=repo_path,
        outputs_manifest=outputs_manifest,
        results_path=results_path,
    )
    pack = ProofPack(
        run_id=episode_id,
        entries=receipts,
        mode=mode,
        suite_id="agentmesh-episode",
        claim_set_id="agentmesh-proof-pack-adapter-v1",
    )
    pack_dir = pack.build(output_dir, keystore=keystore)
    sidecar_dir = _write_sidecars(
        pack_dir,
        outputs_manifest=outputs_manifest,
        results_path=results_path,
        episode_script=episode_script,
    )
    return AgentMeshAssayPackResult(
        pack_dir=pack_dir,
        run_id=episode_id,
        receipt_count=len(receipts),
        output_root_digest=str(outputs_manifest.get("root_digest", "")) if outputs_manifest else "",
        sidecar_dir=sidecar_dir,
    )
