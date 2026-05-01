"""Harness adapter registry. See `.agents/harnesses_spec.md`.

Adapters wrap interactive CLI harnesses (cursor, claude_code, pi,
murder_native) so the runner / Augur / Sentinel can stay
harness-agnostic.
"""

from __future__ import annotations

from murder.harnesses.base import HarnessAdapter
from murder.harnesses.claude_code import ClaudeCodeAdapter
from murder.harnesses.cursor import CursorAdapter
from murder.harnesses.murder_native import MurderNativeAdapter
from murder.harnesses.pi import PiAdapter

REGISTRY: dict[str, type[HarnessAdapter]] = {
    "cursor": CursorAdapter,
    "claude_code": ClaudeCodeAdapter,
    "pi": PiAdapter,
    "murder_native": MurderNativeAdapter,
}


def get(kind: str) -> HarnessAdapter:
    """Instantiate the adapter for `kind`. Raises KeyError if unknown."""
    return REGISTRY[kind]()


__all__ = [
    "HarnessAdapter",
    "REGISTRY",
    "get",
]
