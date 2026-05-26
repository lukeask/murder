from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    """Temporary repo root for filesystem-oriented tests."""
    root = tmp_path / "repo"
    root.mkdir()
    return root
