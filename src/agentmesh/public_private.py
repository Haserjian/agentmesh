"""Deterministic public/private classification helpers."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PUBLIC = "public"
PRIVATE = "private"
REVIEW = "review"


_DEFAULT_PUBLIC_GLOBS = [
    "src/**",
    "tests/**",
    "README.md",
    "LICENSE",
    "docs/spec/**",
    "docs/*.template.json",
    "docs/*.public.json",
    "docs/*.sanitized.json",
]

_DEFAULT_PRIVATE_GLOBS = [
    ".agentmesh/runs/**",
    "docs/alpha-gate-report.json",
    "**/ci-result*.json",
    "**/ci-witness*.log",
    "**/*internal*strategy*",
]

_DEFAULT_REVIEW_GLOBS = [
    "docs/**",
    "scripts/**",
    ".github/**",
]

_DEFAULT_PRIVATE_PATTERNS = [
    r"(^|[^A-Za-z])ghp_[A-Za-z0-9]{20,}",
    r"AKIA[0-9A-Z]{16}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"\bgo[- ]to[- ]market\b",
    r"\bpricing\b",
    r"\bcompetitive positioning\b",
    r"/Users/[A-Za-z0-9._-]+/",
    r"/home/[A-Za-z0-9._-]+/",
]


@dataclass
class Classification:
    path: str
    classification: str
    reasons: list[str] = field(default_factory=list)


def _rel_path(path: Path, repo_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return str(path)


def _policy_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def _load_policy(repo_root: Path) -> dict[str, Any]:
    policy_path = repo_root / ".agentmesh" / "policy.json"
    if not policy_path.exists():
        return {}
    try:
        import json

        data = json.loads(policy_path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _has_match(path: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in globs)


def _content_has_private_marker(text: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE):
            return pattern
    return None


def classify_path(
    path: Path,
    *,
    repo_root: Path,
    policy: dict[str, Any] | None = None,
) -> Classification:
    p = policy or _load_policy(repo_root)
    cfg = p.get("public_private", {}) if isinstance(p, dict) else {}
    if not isinstance(cfg, dict):
        cfg = {}

    public_globs = _policy_list(cfg.get("public_path_globs")) or _DEFAULT_PUBLIC_GLOBS
    private_globs = _policy_list(cfg.get("private_path_globs")) or _DEFAULT_PRIVATE_GLOBS
    review_globs = _policy_list(cfg.get("review_path_globs")) or _DEFAULT_REVIEW_GLOBS
    private_patterns = _policy_list(cfg.get("private_content_patterns")) or _DEFAULT_PRIVATE_PATTERNS

    rel = _rel_path(path, repo_root)
    reasons: list[str] = []

    if _has_match(rel, private_globs):
        reasons.append("path matches private pattern")

    content_marker = None
    if path.exists() and path.is_file():
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            text = ""
        content_marker = _content_has_private_marker(text, private_patterns)
        if content_marker:
            reasons.append(f"content matches private pattern: {content_marker}")

    if reasons:
        return Classification(path=rel, classification=PRIVATE, reasons=reasons)

    if _has_match(rel, public_globs):
        return Classification(path=rel, classification=PUBLIC, reasons=["path matches public pattern"])

    if _has_match(rel, review_globs):
        return Classification(path=rel, classification=REVIEW, reasons=["requires manual review"])

    return Classification(path=rel, classification=REVIEW, reasons=["no explicit policy match"])


def classify_paths(
    paths: list[str],
    *,
    repo_root: Path,
    policy: dict[str, Any] | None = None,
) -> list[Classification]:
    out: list[Classification] = []
    for p in paths:
        out.append(classify_path(repo_root / p, repo_root=repo_root, policy=policy))
    return out
