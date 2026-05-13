"""Harness adapter registry. See `.murder/harnesses_spec.md`.

Adapters wrap interactive CLI harnesses (cursor, claude_code, codex, pi,
murder_native) so the runner / CrowHandler / Sentinel can stay
harness-agnostic.
"""

from __future__ import annotations

from murder.harnesses.base import (
    HarnessAdapter,
    HarnessSession,
)
from murder.harnesses.models import HarnessPaneState, HarnessStartSpec
from murder.harnesses.results import SimpleResult
from murder.harnesses.claude_code import ClaudeCodeAdapter
from murder.harnesses.codex import CodexAdapter
from murder.harnesses.cursor import CursorAdapter
from murder.harnesses.murder_native import MurderNativeAdapter
from murder.harnesses.pi import PiAdapter

REGISTRY: dict[str, type[HarnessAdapter]] = {
    "cursor": CursorAdapter,
    "claude_code": ClaudeCodeAdapter,
    "codex": CodexAdapter,
    "pi": PiAdapter,
    "murder_native": MurderNativeAdapter,
}


def get(kind: str, startup_model: str | None = None) -> HarnessAdapter:
    """Instantiate the adapter for `kind`. Raises KeyError if unknown."""
    return REGISTRY[kind](startup_model=startup_model)


__all__ = [
    "HarnessAdapter",
    "HarnessSession",
    "HarnessPaneState",
    "HarnessStartSpec",
    "SimpleResult",
    "REGISTRY",
    "get",
]
