"""Git bridge -- helpers for agentmesh commit to wrap git operations."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


def _run_git(args: list[str], cwd: str | None = None) -> str:
    """Run a git command and return stdout. Returns empty string on failure."""
    try:
        result = subprocess.run(
            ["git"] + args, capture_output=True, text=True, timeout=10,
            cwd=cwd,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def _run_git_rc(
    args: list[str], cwd: str | None = None,
) -> tuple[int, str, str]:
    """Run a git command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["git"] + args, capture_output=True, text=True, timeout=30,
            cwd=cwd,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return 1, "", str(exc)


def is_git_repo(cwd: str | None = None) -> bool:
    """Check if cwd is inside a git repository."""
    return _run_git(["rev-parse", "--is-inside-work-tree"], cwd=cwd) == "true"


def get_staged_diff(cwd: str | None = None) -> str:
    """Return the staged diff (git diff --cached)."""
    return _run_git(["diff", "--cached"], cwd=cwd)


def get_staged_files(cwd: str | None = None) -> list[str]:
    """Return list of staged file paths (git diff --cached --name-only)."""
    out = _run_git(["diff", "--cached", "--name-only"], cwd=cwd)
    if not out:
        return []
    return [line for line in out.splitlines() if line.strip()]


def compute_patch_hash(diff_text: str) -> str:
    """SHA-256 hash of diff text. Returns sha256:<hex>."""
    h = hashlib.sha256(diff_text.encode()).hexdigest()
    return f"sha256:{h}"


def compute_patch_id_stable(diff_text: str, cwd: str | None = None) -> str | None:
    """Compute git patch-id --stable from diff text. Returns hex string or None."""
    if not diff_text:
        return None
    try:
        result = subprocess.run(
            ["git", "patch-id", "--stable"],
            input=diff_text, capture_output=True, text=True, timeout=10,
            cwd=cwd,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        # Output format: "<patch-id> <commit-sha-or-zero>"
        return result.stdout.strip().split()[0]
    except (subprocess.TimeoutExpired, FileNotFoundError, IndexError):
        return None


def git_commit(
    message: str,
    extra_args: list[str] | None = None,
    trailer: str = "",
    cwd: str | None = None,
) -> tuple[bool, str, str]:
    """Run git commit. Returns (success, sha, error).

    If trailer is non-empty, it is appended to the message after a blank line.
    """
    full_message = message
    if trailer:
        full_message = f"{message}\n\n{trailer}"

    args = ["commit", "-m", full_message]
    if extra_args:
        args.extend(extra_args)

    rc, stdout, stderr = _run_git_rc(args, cwd=cwd)
    if rc != 0:
        return False, "", stderr

    sha = _run_git(["rev-parse", "HEAD"], cwd=cwd)
    return True, sha, ""


def run_tests(command: str, cwd: str | None = None) -> tuple[bool, str]:
    """Run a test command. Returns (passed, summary).

    Summary is last 20 lines of combined output.
    """
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=300, cwd=cwd,
        )
        lines = (result.stdout + "\n" + result.stderr).strip().splitlines()
        summary = "\n".join(lines[-20:])
        return result.returncode == 0, summary
    except subprocess.TimeoutExpired:
        return False, "Test command timed out (300s)"
    except Exception as exc:
        return False, str(exc)
