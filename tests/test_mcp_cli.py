"""CLI tests for MCP command behavior."""

from __future__ import annotations

import builtins

from typer.testing import CliRunner

from agentmesh.cli import app

runner = CliRunner()


def test_mcp_serve_missing_dependency_shows_install_extra(monkeypatch) -> None:
    """Missing optional MCP deps should point users to the [mcp] extra."""
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if isinstance(name, str) and name.endswith("mcp_server"):
            raise ImportError("mcp optional dependency missing")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    result = runner.invoke(app, ["mcp", "serve"])
    assert result.exit_code == 1
    assert "agentmesh-core[mcp]" in result.output
