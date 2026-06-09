"""Shared CLI utilities used by multiple command modules."""

from __future__ import annotations

from pathlib import Path


def repo_root() -> Path:
    """Return the resolved current working directory as the project root."""
    return Path.cwd().resolve()
