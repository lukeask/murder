"""Grading-specific errors (shared by adapter and fakes)."""

from __future__ import annotations


class GraderOutputError(ValueError):
    """Raised when structured grading output cannot be validated after retries."""


__all__ = [
    "GraderOutputError",
]
