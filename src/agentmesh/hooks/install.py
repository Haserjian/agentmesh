"""Idempotent Claude Code hook installer/uninstaller."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

_HOOKS_DIR = Path(__file__).parent
_CLAUDE_HOOKS_DIR = Path.home() / ".claude" / "hooks"
_CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"

_HOOK_FILES = {
    "agentmesh-pre-edit.sh": "agentmesh_pre_edit.sh",
    "agentmesh-post-edit.sh": "agentmesh_post_edit.sh",
}

_MARKER = "agentmesh"

_HOOK_CONFIG = {
    "PreToolUse": [
        {
            "matcher": "Edit|Write",
            "hooks": [
                {
                    "type": "command",
                    "command": str(_CLAUDE_HOOKS_DIR / "agentmesh-pre-edit.sh"),
                }
            ],
        }
    ],
    "PostToolUse": [
        {
            "matcher": "Edit|Write",
            "hooks": [
                {
                    "type": "command",
                    "command": str(_CLAUDE_HOOKS_DIR / "agentmesh-post-edit.sh"),
                }
            ],
        }
    ],
}


def install_hooks() -> list[str]:
    """Install hooks. Returns list of actions taken."""
    actions = []

    # Copy hook scripts
    _CLAUDE_HOOKS_DIR.mkdir(parents=True, exist_ok=True)
    for dest_name, src_name in _HOOK_FILES.items():
        src = _HOOKS_DIR / src_name
        dest = _CLAUDE_HOOKS_DIR / dest_name
        shutil.copy2(str(src), str(dest))
        dest.chmod(0o755)
        actions.append(f"Copied {dest_name}")

    # Update settings.json
    settings = _load_settings()
    hooks = settings.get("hooks", {})

    for event_type, hook_entries in _HOOK_CONFIG.items():
        existing = hooks.get(event_type, [])
        # Remove any existing agentmesh hooks
        existing = [h for h in existing if not _is_agentmesh_hook(h)]
        existing.extend(hook_entries)
        hooks[event_type] = existing

    settings["hooks"] = hooks
    _save_settings(settings)
    actions.append("Updated settings.json")

    return actions


def uninstall_hooks() -> list[str]:
    """Remove hooks. Returns list of actions taken."""
    actions = []

    # Remove hook scripts
    for dest_name in _HOOK_FILES:
        dest = _CLAUDE_HOOKS_DIR / dest_name
        if dest.exists():
            dest.unlink()
            actions.append(f"Removed {dest_name}")

    # Update settings.json
    settings = _load_settings()
    hooks = settings.get("hooks", {})

    for event_type in list(hooks.keys()):
        hooks[event_type] = [h for h in hooks[event_type] if not _is_agentmesh_hook(h)]
        if not hooks[event_type]:
            del hooks[event_type]

    if hooks:
        settings["hooks"] = hooks
    elif "hooks" in settings:
        del settings["hooks"]

    _save_settings(settings)
    actions.append("Updated settings.json")

    return actions


def hooks_status() -> dict:
    """Check hook installation status."""
    scripts_ok = all(
        (_CLAUDE_HOOKS_DIR / name).exists() for name in _HOOK_FILES
    )
    settings = _load_settings()
    hooks = settings.get("hooks", {})
    settings_ok = any(
        _is_agentmesh_hook(h)
        for entries in hooks.values()
        for h in entries
    )
    return {
        "installed": scripts_ok and settings_ok,
        "scripts_present": scripts_ok,
        "settings_configured": settings_ok,
    }


def _is_agentmesh_hook(entry: dict) -> bool:
    """Check if a hook entry belongs to agentmesh."""
    for h in entry.get("hooks", []):
        if _MARKER in h.get("command", ""):
            return True
    return False


def _load_settings() -> dict:
    if not _CLAUDE_SETTINGS.exists():
        return {}
    return json.loads(_CLAUDE_SETTINGS.read_text())


def _save_settings(settings: dict) -> None:
    _CLAUDE_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    _CLAUDE_SETTINGS.write_text(json.dumps(settings, indent=2) + "\n")
