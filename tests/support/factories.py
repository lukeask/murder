from __future__ import annotations

from pathlib import Path


def make_repo_root(tmp_path: Path, name: str = "repo") -> Path:
    """Create a disposable repo root for tests that exercise .murder state."""
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    return root
