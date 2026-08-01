"""Shared strict main-database fixture for codebase-map tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from murder.state.persistence.connection import RepoDb
from tests.support.database import open_test_repo_db


@pytest.fixture
def repo_db(tmp_path: Path) -> Iterator[RepoDb]:
    """A deterministic repository partition in a local Turso database."""
    db = open_test_repo_db(tmp_path / "murder.db")
    try:
        yield db
    finally:
        db.close()
