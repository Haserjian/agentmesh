"""Tests for secret/sensitive-content detection via classify_path().

Each test uses a real fixture file from tests/fixtures/secrets/ and verifies
that the content-scanning branch of classify_path() correctly flags private
patterns (or does not overfire on clean controls).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentmesh.public_private import PRIVATE, PUBLIC, classify_path

FIXTURES = Path(__file__).parent / "fixtures" / "secrets"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _classify_fixture(name: str, tmp_path: Path) -> tuple[str, list[str]]:
    """Copy a fixture into a fake repo under src/ and classify it.

    Placing the file under src/ ensures the *path* would match the default
    PUBLIC glob.  Any PRIVATE result therefore proves that content scanning
    overrode the path classification.
    """
    fixture = FIXTURES / name
    repo = tmp_path / "repo"
    src = repo / "src"
    src.mkdir(parents=True)
    dest = src / name
    dest.write_bytes(fixture.read_bytes())
    result = classify_path(dest, repo_root=repo)
    return result.classification, result.reasons


# ---------------------------------------------------------------------------
# Parameterised: specimens that MUST be classified as PRIVATE
# ---------------------------------------------------------------------------

_PRIVATE_SPECIMENS = [
    pytest.param(
        "ghp_token.py",
        r"ghp_[A-Za-z0-9]{20,}",
        id="github-pat",
    ),
    pytest.param(
        "aws_key.py",
        r"AKIA[0-9A-Z]{16}",
        id="aws-access-key",
    ),
    pytest.param(
        "private_key.pem",
        r"PRIVATE KEY",
        id="pem-private-key",
    ),
    pytest.param(
        "pricing_doc.md",
        r"pricing",
        id="business-pricing",
    ),
    pytest.param(
        "edge_ghp_in_comment.py",
        r"ghp_[A-Za-z0-9]{20,}",
        id="ghp-in-comment",
    ),
]


@pytest.mark.parametrize("fixture_name, expected_pattern_fragment", _PRIVATE_SPECIMENS)
def test_private_specimen_detected(
    fixture_name: str,
    expected_pattern_fragment: str,
    tmp_path: Path,
) -> None:
    classification, reasons = _classify_fixture(fixture_name, tmp_path)
    assert classification == PRIVATE, (
        f"{fixture_name} should be PRIVATE but got {classification}"
    )
    combined = " ".join(reasons)
    assert "content matches private pattern" in combined, (
        f"expected content-based reason, got {reasons}"
    )
    assert expected_pattern_fragment.lower() in combined.lower(), (
        f"expected pattern fragment {expected_pattern_fragment!r} in reasons: {reasons}"
    )


# ---------------------------------------------------------------------------
# Clean control: must NOT be classified as PRIVATE
# ---------------------------------------------------------------------------

def test_clean_public_not_private(tmp_path: Path) -> None:
    classification, reasons = _classify_fixture("clean_public.py", tmp_path)
    assert classification == PUBLIC, (
        f"clean_public.py should be PUBLIC but got {classification}: {reasons}"
    )
    for reason in reasons:
        assert "content matches private pattern" not in reason


# ---------------------------------------------------------------------------
# Additional business-sensitive pattern coverage
# ---------------------------------------------------------------------------

_BUSINESS_SENSITIVE_CONTENT = [
    pytest.param("Our go to market strategy is bold.\n", r"go[- ]to[- ]market", id="go-to-market-spaces"),
    pytest.param("The go-to-market plan launches Q3.\n", r"go[- ]to[- ]market", id="go-to-market-hyphens"),
    pytest.param("Competitive positioning against X.\n", r"competitive positioning", id="competitive-positioning"),
]


@pytest.mark.parametrize("content, expected_fragment", _BUSINESS_SENSITIVE_CONTENT)
def test_business_sensitive_inline(
    content: str,
    expected_fragment: str,
    tmp_path: Path,
) -> None:
    """Verify business-sensitive phrases trigger PRIVATE even under src/."""
    repo = tmp_path / "repo"
    src = repo / "src"
    src.mkdir(parents=True)
    target = src / "memo.txt"
    target.write_text(content)
    result = classify_path(target, repo_root=repo)
    assert result.classification == PRIVATE
    combined = " ".join(result.reasons).lower()
    assert expected_fragment.lower() in combined


# ---------------------------------------------------------------------------
# Home-path leakage pattern
# ---------------------------------------------------------------------------

_HOME_PATH_CONTENT = [
    pytest.param("/Users/developer/project/data.csv\n", r"/Users/", id="macos-home"),
    pytest.param("/home/deploy/.config/secret.yml\n", r"/home/", id="linux-home"),
]


@pytest.mark.parametrize("content, expected_fragment", _HOME_PATH_CONTENT)
def test_home_path_leakage(
    content: str,
    expected_fragment: str,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    src = repo / "src"
    src.mkdir(parents=True)
    target = src / "config.txt"
    target.write_text(content)
    result = classify_path(target, repo_root=repo)
    assert result.classification == PRIVATE
    combined = " ".join(result.reasons)
    assert expected_fragment in combined
