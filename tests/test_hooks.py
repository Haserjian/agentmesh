"""Tests for Claude Code hook integration."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

from agentmesh.hooks import install as hook_install


def test_hook_scripts_exist() -> None:
    """Hook shell scripts should exist in the package."""
    hooks_dir = Path(__file__).parent.parent / "src" / "agentmesh" / "hooks"
    assert (hooks_dir / "agentmesh_pre_edit.sh").exists()
    assert (hooks_dir / "agentmesh_post_edit.sh").exists()


def test_install_and_uninstall(tmp_path: Path) -> None:
    """Install should copy scripts and update settings; uninstall reverses."""
    hooks_dir = tmp_path / "hooks"
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{}")

    with mock.patch.object(hook_install, "_CLAUDE_HOOKS_DIR", hooks_dir), \
         mock.patch.object(hook_install, "_CLAUDE_SETTINGS", settings_file):
        # Install
        actions = hook_install.install_hooks()
        assert any("Copied" in a for a in actions)
        assert (hooks_dir / "agentmesh-pre-edit.sh").exists()
        assert (hooks_dir / "agentmesh-post-edit.sh").exists()

        settings = json.loads(settings_file.read_text())
        assert "hooks" in settings
        assert "PreToolUse" in settings["hooks"]

        # Status
        status = hook_install.hooks_status()
        assert status["installed"]

        # Uninstall
        actions2 = hook_install.uninstall_hooks()
        assert any("Removed" in a for a in actions2)
        assert not (hooks_dir / "agentmesh-pre-edit.sh").exists()

        status2 = hook_install.hooks_status()
        assert not status2["installed"]


def test_install_preserves_existing_hooks(tmp_path: Path) -> None:
    """Install should not clobber existing non-agentmesh hooks."""
    hooks_dir = tmp_path / "hooks"
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo test"}]}
            ]
        }
    }))

    with mock.patch.object(hook_install, "_CLAUDE_HOOKS_DIR", hooks_dir), \
         mock.patch.object(hook_install, "_CLAUDE_SETTINGS", settings_file):
        hook_install.install_hooks()
        settings = json.loads(settings_file.read_text())
        pre = settings["hooks"]["PreToolUse"]
        # Should have both the existing hook and the agentmesh hook
        assert len(pre) == 2
        assert any("echo test" in str(h) for h in pre)
        assert any("agentmesh" in str(h) for h in pre)
