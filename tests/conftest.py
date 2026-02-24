"""Shared fixtures for AgentMesh tests."""

from __future__ import annotations

import pytest
from pathlib import Path

from agentmesh import db


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Provide a temporary data directory with initialized DB."""
    data_dir = tmp_path / "agentmesh"
    data_dir.mkdir()
    db.init_db(data_dir)
    return data_dir
