"""Shared test helpers: locate and load files from tests/fixtures/."""
from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    """Absolute path to the tests/fixtures directory."""
    return FIXTURES


@pytest.fixture
def fixture_bytes():
    """Load a fixture file as raw bytes: fixture_bytes('form4_full_submission.txt')."""
    def _load(name: str) -> bytes:
        return (FIXTURES / name).read_bytes()
    return _load


@pytest.fixture
def fixture_text():
    """Load a fixture file as utf-8 text."""
    def _load(name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")
    return _load
