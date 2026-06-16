"""Completion check system: coordinator + registry over per-ticket checks."""

from .coordinator import CompletionCoordinator, CoordinatorHost, DoneHandleResult
from .registry import CheckRegistry

__all__ = [
    "CheckRegistry",
    "CompletionCoordinator",
    "CoordinatorHost",
    "DoneHandleResult",
]
