"""Soft-conflict detection: exported symbol changes + dependent file alerts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .models import Claim, EventKind, Severity
from . import db, events, messages


# -- Python export scanning --

_PY_DEF = re.compile(r"^(?:def|class)\s+([A-Za-z_]\w*)", re.MULTILINE)
_PY_ASSIGN = re.compile(r"^([A-Z_][A-Z_0-9]*)\s*=", re.MULTILINE)

# -- JS/TS export scanning --

_JS_EXPORT_NAMED = re.compile(
    r"^export\s+(?:function|class|const|let|var|type|interface|enum)\s+([A-Za-z_$]\w*)",
    re.MULTILINE,
)
_JS_EXPORT_DEFAULT = re.compile(
    r"^export\s+default\s+(?:function|class)\s+([A-Za-z_$]\w*)",
    re.MULTILINE,
)

# -- Python import scanning --

_PY_FROM_IMPORT = re.compile(
    r"^from\s+([\w.]+)\s+import\s+(.+)", re.MULTILINE
)
_PY_IMPORT = re.compile(r"^import\s+([\w.]+)", re.MULTILINE)


def scan_exports(file_path: str) -> set[str]:
    """Scan a file for exported symbol names.

    Python: top-level def, class, UPPER_CASE assignments.
    JS/TS: export statements.
    """
    p = Path(file_path)
    if not p.exists():
        return set()
    content = p.read_text(errors="replace")
    suffix = p.suffix.lower()

    symbols: set[str] = set()

    if suffix in (".py",):
        symbols.update(m.group(1) for m in _PY_DEF.finditer(content))
        symbols.update(m.group(1) for m in _PY_ASSIGN.finditer(content))
    elif suffix in (".js", ".ts", ".tsx", ".jsx", ".mjs"):
        symbols.update(m.group(1) for m in _JS_EXPORT_NAMED.finditer(content))
        symbols.update(m.group(1) for m in _JS_EXPORT_DEFAULT.finditer(content))

    return symbols


def scan_imports(file_path: str) -> set[tuple[str, str]]:
    """Scan a Python file for imported symbols.

    Returns set of (module, symbol) tuples.
    """
    p = Path(file_path)
    if not p.exists():
        return set()
    content = p.read_text(errors="replace")
    suffix = p.suffix.lower()

    imports: set[tuple[str, str]] = set()

    if suffix in (".py",):
        for m in _PY_FROM_IMPORT.finditer(content):
            module = m.group(1)
            names = m.group(2)
            for name in re.split(r",\s*", names.strip().rstrip("\\")):
                name = name.strip()
                if name and not name.startswith("("):
                    clean = name.split(" as ")[0].strip()
                    if clean:
                        imports.add((module, clean))
        for m in _PY_IMPORT.finditer(content):
            module = m.group(1)
            imports.add((module, module.split(".")[-1]))

    return imports


def detect_symbol_changes(
    file_path: str, before_content: str, after_content: str,
) -> list[str]:
    """Detect added/removed exported symbols between two versions.

    Returns list of "+symbol" (added) or "-symbol" (removed) strings.
    """
    suffix = Path(file_path).suffix.lower()

    def _extract(content: str) -> set[str]:
        symbols: set[str] = set()
        if suffix in (".py",):
            symbols.update(m.group(1) for m in _PY_DEF.finditer(content))
            symbols.update(m.group(1) for m in _PY_ASSIGN.finditer(content))
        elif suffix in (".js", ".ts", ".tsx", ".jsx", ".mjs"):
            symbols.update(m.group(1) for m in _JS_EXPORT_NAMED.finditer(content))
            symbols.update(m.group(1) for m in _JS_EXPORT_DEFAULT.finditer(content))
        return symbols

    before = _extract(before_content)
    after = _extract(after_content)

    changes: list[str] = []
    for s in sorted(after - before):
        changes.append(f"+{s}")
    for s in sorted(before - after):
        changes.append(f"-{s}")
    return changes


def find_dependents(
    changed_file: str,
    changed_symbols: list[str],
    claimed_paths: list[Claim],
) -> list[tuple[str, Claim]]:
    """Find agents whose claimed files import any of the changed symbols.

    Returns list of (agent_id, claim) for affected files.
    """
    removed = {s[1:] for s in changed_symbols if s.startswith("-")}
    if not removed:
        return []

    changed_stem = Path(changed_file).stem
    changed_module = Path(changed_file).with_suffix("").name

    affected: list[tuple[str, Claim]] = []
    for claim in claimed_paths:
        if claim.path == changed_file:
            continue
        imports = scan_imports(claim.path)
        for module, symbol in imports:
            if symbol in removed and (
                module.endswith(changed_stem) or module.endswith(changed_module)
            ):
                affected.append((claim.agent_id, claim))
                break

    return affected


def post_soft_conflict_alerts(
    changed_file: str,
    changed_symbols: list[str],
    agent_id: str,
    data_dir: Path | None = None,
) -> int:
    """Post ATTN messages to agents with files that import removed symbols.

    Returns count of alerts posted.
    """
    active_claims = db.list_claims(data_dir, active_only=True)
    other_claims = [c for c in active_claims if c.agent_id != agent_id]

    affected = find_dependents(changed_file, changed_symbols, other_claims)

    count = 0
    removed = [s for s in changed_symbols if s.startswith("-")]
    for target_agent, claim in affected:
        body = (
            f"Soft conflict: {changed_file} changed symbols {', '.join(removed)}. "
            f"Your file {claim.path} may import these."
        )
        messages.post(
            agent_id, body,
            to_agent=target_agent, severity=Severity.ATTN,
            data_dir=data_dir,
        )
        events.append_event(
            EventKind.SOFT_CONFLICT, agent_id=agent_id,
            payload={
                "changed_file": changed_file,
                "symbols": removed,
                "affected_agent": target_agent,
                "affected_file": claim.path,
            },
            data_dir=data_dir,
        )
        count += 1

    return count
