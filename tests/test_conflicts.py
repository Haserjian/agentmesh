"""Tests for soft-conflict detection -- 5 scenarios."""

from __future__ import annotations

from pathlib import Path

from agentmesh import db
from agentmesh.claims import make_claim
from agentmesh.conflicts import (
    scan_exports,
    scan_imports,
    detect_symbol_changes,
    find_dependents,
    post_soft_conflict_alerts,
)
from agentmesh.models import Agent, Claim, ClaimIntent, ClaimState, ResourceType


def _register(agent_id: str, data_dir: Path) -> None:
    db.register_agent(Agent(agent_id=agent_id, cwd="/tmp"), data_dir)


def test_scan_python_exports(tmp_path: Path) -> None:
    """Should find def, class, and UPPER assignments."""
    src = tmp_path / "module.py"
    src.write_text(
        "def foo():\n    pass\n\n"
        "class Bar:\n    pass\n\n"
        "MAX_SIZE = 100\n"
        "local_var = 1\n"  # lowercase, should not be found
    )
    exports = scan_exports(str(src))
    assert "foo" in exports
    assert "Bar" in exports
    assert "MAX_SIZE" in exports
    assert "local_var" not in exports


def test_scan_imports(tmp_path: Path) -> None:
    """Should find from-import and plain import."""
    src = tmp_path / "consumer.py"
    src.write_text(
        "from models import Agent, Claim\n"
        "import json\n"
    )
    imports = scan_imports(str(src))
    assert ("models", "Agent") in imports
    assert ("models", "Claim") in imports
    assert ("json", "json") in imports


def test_detect_added_symbols() -> None:
    """Should detect symbols added between versions."""
    before = "def foo():\n    pass\n"
    after = "def foo():\n    pass\n\ndef bar():\n    pass\n"
    changes = detect_symbol_changes("test.py", before, after)
    assert "+bar" in changes
    assert "-foo" not in changes  # foo still exists


def test_detect_removed_symbols() -> None:
    """Should detect symbols removed between versions."""
    before = "def foo():\n    pass\n\ndef bar():\n    pass\n"
    after = "def foo():\n    pass\n"
    changes = detect_symbol_changes("test.py", before, after)
    assert "-bar" in changes
    assert "+foo" not in changes  # foo still exists


def test_alert_posting_to_dependents(tmp_data_dir: Path, tmp_path: Path) -> None:
    """Should post ATTN messages when an agent's file imports a removed symbol."""
    _register("a1", tmp_data_dir)
    _register("a2", tmp_data_dir)

    # a1 owns models.py (changed)
    models_file = tmp_path / "models.py"
    models_file.write_text("def Agent():\n    pass\n\ndef OldModel():\n    pass\n")

    # a2 owns consumer.py which imports OldModel from models
    consumer_file = tmp_path / "consumer.py"
    consumer_file.write_text("from models import OldModel\n")

    make_claim("a1", str(models_file), data_dir=tmp_data_dir)
    make_claim("a2", str(consumer_file), data_dir=tmp_data_dir)

    count = post_soft_conflict_alerts(
        str(models_file), ["-OldModel"], "a1", data_dir=tmp_data_dir,
    )
    assert count == 1

    # Verify the message was posted
    msgs = db.list_messages(tmp_data_dir, to_agent="a2")
    assert any("Soft conflict" in m.body for m in msgs)
