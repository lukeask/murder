"""Completion check system — replaces ValidatorPipeline."""

from .coordinator import CompletionCoordinator, CoordinatorHost
from .registry import CheckRegistry

__all__ = [
    "CheckRegistry",
    "CompletionCoordinator",
    "CoordinatorHost",
]
