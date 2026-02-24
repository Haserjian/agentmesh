"""Pluggable worker backend adapters for the spawner."""

from __future__ import annotations

import importlib
import json
import os
from inspect import getmodule, getsourcefile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from . import __version__ as _agentmesh_version


@dataclass
class SpawnSpec:
    """What to run: command, output path, extra env vars."""

    command: list[str]
    output_path: str
    env: dict[str, str] = field(default_factory=dict)
    stdout_to_file: bool = True  # spawner redirects stdout to output_path


@dataclass
class WorkerOutput:
    """Structured output from a worker adapter's parse_output.

    Adapters may return this directly from parse_output, or return the
    legacy tuple[bool, dict] format.  Use normalize_worker_output() to
    convert either format into a WorkerOutput.
    """

    success: bool
    raw: dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    error_message: str = ""


@dataclass(frozen=True)
class AdapterInfo:
    name: str
    version: str
    module: str = ""
    origin: str = ""


@runtime_checkable
class WorkerAdapter(Protocol):
    """Protocol that worker backends must satisfy."""

    name: str
    version: str

    def build_spawn_spec(
        self,
        *,
        context: str,
        model: str,
        worktree_path: Path,
        output_dir: Path,
    ) -> SpawnSpec: ...

    def parse_output(
        self,
        output_path: Path,
    ) -> "WorkerOutput | tuple[bool, dict[str, Any]]": ...


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class ClaudeCodeAdapter:
    """Default adapter: Claude Code CLI."""

    name: str = "claude_code"
    version: str = f"agentmesh:{_agentmesh_version}"

    def build_spawn_spec(
        self,
        *,
        context: str,
        model: str,
        worktree_path: Path,
        output_dir: Path,
    ) -> SpawnSpec:
        output_path = str(output_dir / "claude_output.json")
        cmd = [
            "claude",
            "--print",
            "--output-format", "json",
            "--model", model,
            "--dangerously-skip-permissions",
            context,
        ]
        return SpawnSpec(command=cmd, output_path=output_path)

    def parse_output(self, output_path: Path) -> WorkerOutput:
        if not output_path.exists():
            return WorkerOutput(success=False, error_message="output file missing")
        try:
            content = output_path.read_text().strip()
            if not content:
                return WorkerOutput(success=False, error_message="output file empty")
            data = json.loads(content)
            return WorkerOutput(
                success=True,
                raw=data,
                cost_usd=_to_float(data.get("cost_usd", 0.0)),
                tokens_in=_to_int(data.get("num_input_tokens", 0)),
                tokens_out=_to_int(data.get("num_output_tokens", 0)),
            )
        except (json.JSONDecodeError, OSError) as exc:
            return WorkerOutput(success=False, error_message=str(exc))


# ---------------------------------------------------------------------------
# Compatibility shim
# ---------------------------------------------------------------------------

def normalize_worker_output(result: WorkerOutput | tuple[bool, dict[str, Any]]) -> WorkerOutput:
    """Convert adapter parse_output return value to WorkerOutput.

    Accepts either a WorkerOutput (pass-through) or the legacy
    tuple[bool, dict] format from custom adapters.
    """
    if isinstance(result, WorkerOutput):
        return result
    success, data = result
    if not isinstance(data, dict):
        data = {}
    return WorkerOutput(
        success=success,
        raw=data,
        cost_usd=_to_float(data.get("cost_usd", 0.0)),
        tokens_in=_to_int(data.get("num_input_tokens", 0)),
        tokens_out=_to_int(data.get("num_output_tokens", 0)),
        error_message=data.get("error", ""),
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_ADAPTERS: dict[str, WorkerAdapter] = {}
_ADAPTER_LOAD_ERRORS: list[str] = []


def register_adapter(adapter: WorkerAdapter) -> None:
    """Register a worker adapter by its name."""
    if not getattr(adapter, "name", ""):
        raise ValueError("adapter.name is required")
    _ADAPTERS[adapter.name] = adapter


def get_adapter(name: str) -> WorkerAdapter:
    """Look up a registered adapter. Raises ValueError if not found."""
    if name not in _ADAPTERS:
        known = ", ".join(sorted(_ADAPTERS)) or "(none)"
        raise ValueError(f"Unknown worker backend {name!r}. Registered: {known}")
    return _ADAPTERS[name]


def list_adapters() -> list[AdapterInfo]:
    """List registered backends with versions."""
    out: list[AdapterInfo] = []
    for name in sorted(_ADAPTERS):
        adapter = _ADAPTERS[name]
        mod = adapter.__class__.__module__
        origin = _adapter_origin(adapter)
        out.append(
            AdapterInfo(
                name=name,
                version=getattr(adapter, "version", "") or "",
                module=mod,
                origin=origin,
            )
        )
    return out


def get_adapter_load_errors() -> list[str]:
    """Return module autoload errors captured at import time."""
    return list(_ADAPTER_LOAD_ERRORS)


def _adapter_origin(adapter: WorkerAdapter) -> str:
    cls = adapter.__class__
    module = getmodule(cls)
    path = getsourcefile(cls) or getattr(module, "__file__", "") or ""
    try:
        return str(Path(path).resolve()) if path else ""
    except OSError:
        return path


def describe_adapter(name: str) -> AdapterInfo:
    adapter = get_adapter(name)
    return AdapterInfo(
        name=name,
        version=getattr(adapter, "version", "") or "",
        module=adapter.__class__.__module__,
        origin=_adapter_origin(adapter),
    )


def _read_policy(repo_cwd: str | Path | None) -> dict[str, Any]:
    if not repo_cwd:
        return {}
    path = Path(repo_cwd) / ".agentmesh" / "policy.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _policy_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def enforce_adapter_policy(
    backend: str,
    *,
    repo_cwd: str | Path | None = None,
    policy: dict[str, Any] | None = None,
) -> None:
    """Fail-closed adapter policy gate.

    Policy keys (optional) in `.agentmesh/policy.json`:
      worker_adapters.allow_backends: ["claude_code", ...]
      worker_adapters.allow_modules: ["agentmesh.worker_adapters", ...]
      worker_adapters.allow_paths: ["/abs/path/prefix", "./relative/prefix", ...]
    Empty/missing lists mean "no restriction" for that key.
    """
    cfg = (policy or _read_policy(repo_cwd)).get("worker_adapters", {})
    if not isinstance(cfg, dict):
        cfg = {}

    info = describe_adapter(backend)

    allowed_backends = _policy_list(cfg.get("allow_backends"))
    if allowed_backends and backend not in allowed_backends:
        raise ValueError(
            f"Backend {backend!r} is disallowed by policy allow_backends"
        )

    allowed_modules = _policy_list(cfg.get("allow_modules"))
    if allowed_modules and info.module not in allowed_modules:
        raise ValueError(
            f"Backend {backend!r} module {info.module!r} is disallowed by policy allow_modules"
        )

    allowed_paths = _policy_list(cfg.get("allow_paths"))
    if allowed_paths:
        if not info.origin:
            raise ValueError(
                f"Backend {backend!r} has unknown origin path; denied by allow_paths policy"
            )
        origin = Path(info.origin).resolve()
        repo_base = Path(repo_cwd).resolve() if repo_cwd else None
        path_ok = False
        for raw in allowed_paths:
            base = Path(raw)
            if not base.is_absolute():
                if repo_base is None:
                    continue
                base = (repo_base / base).resolve()
            else:
                base = base.resolve()
            try:
                origin.relative_to(base)
                path_ok = True
                break
            except ValueError:
                continue
        if not path_ok:
            raise ValueError(
                f"Backend {backend!r} origin {info.origin!r} is disallowed by policy allow_paths"
            )


def _register_from_module(module: Any) -> int:
    """Register adapters exported by a module. Returns number of new adapters."""
    before = set(_ADAPTERS)

    # explicit hook: def register_adapters(register_adapter): ...
    register_fn = getattr(module, "register_adapters", None)
    if callable(register_fn):
        register_fn(register_adapter)

    # iterable hook: ADAPTERS = [AdapterA(), AdapterB()]
    exported = getattr(module, "ADAPTERS", None)
    if exported is not None:
        for adapter in exported:
            register_adapter(adapter)

    # side-effect-only modules are also supported.
    return len(set(_ADAPTERS) - before)


def load_adapters_from_modules(module_names: list[str]) -> list[str]:
    """Import adapter modules and register any exported adapters.

    Returns newly registered adapter names.
    """
    new_names: set[str] = set()
    for module_name in module_names:
        name = module_name.strip()
        if not name:
            continue
        try:
            before = set(_ADAPTERS)
            module = importlib.import_module(name)
            _register_from_module(module)
            after = set(_ADAPTERS)
            new_names.update(after - before)
        except Exception as exc:
            _ADAPTER_LOAD_ERRORS.append(f"{name}: {exc}")
    return sorted(new_names)


def load_adapters_from_env(env_var: str = "AGENTMESH_ADAPTER_MODULES") -> list[str]:
    """Load adapters from comma-separated module names in env var."""
    if os.getenv("CI", "").strip().lower() in {"1", "true", "yes", "on"}:
        _ADAPTER_LOAD_ERRORS.append(f"{env_var}: disabled in CI=true")
        return []
    raw = os.getenv(env_var, "").strip()
    if not raw:
        return []
    modules = [part.strip() for part in raw.split(",") if part.strip()]
    return load_adapters_from_modules(modules)


# Auto-register the built-in adapter.
register_adapter(ClaudeCodeAdapter())
load_adapters_from_env()
