"""Harness adapter registry. See `.murder/harnesses_spec.md`.

Adapters wrap interactive CLI harnesses (cursor, claude_code, codex, pi,
antigravity, native_coding_crow) so the runner / CrowHandler / Sentinel
can stay harness-agnostic.
"""

from __future__ import annotations

from murder.llm.harnesses.antigravity import AntigravityAdapter
from murder.llm.harnesses.base import (
    HarnessAdapter,
    HarnessSession,
)
from murder.llm.harnesses.capabilities import CapabilityError, HarnessCapabilities, require
from murder.llm.harnesses.claude_code import ClaudeCodeAdapter
from murder.llm.harnesses.codex import CodexAdapter
from murder.llm.harnesses.cursor import CursorAdapter
from murder.llm.harnesses.models import HarnessPaneState, HarnessStartSpec
from murder.llm.harnesses.native_coding_crow import NativeCodingCrowAdapter
from murder.llm.harnesses.pi_harness import PiAdapter
from murder.llm.harnesses.results import SimpleResult

REGISTRY: dict[str, type[HarnessAdapter]] = {
    "cursor": CursorAdapter,
    "claude_code": ClaudeCodeAdapter,
    "codex": CodexAdapter,
    "pi": PiAdapter,
    "antigravity": AntigravityAdapter,
    "native_coding_crow": NativeCodingCrowAdapter,
}

CAPABILITY_REGISTRY: dict[str, HarnessCapabilities] = {
    kind: adapter_cls.declared_capabilities() for kind, adapter_cls in REGISTRY.items()
}


def capabilities_for(kind: str) -> HarnessCapabilities:
    """Return declared capabilities for a harness kind."""
    try:
        return CAPABILITY_REGISTRY[kind]
    except KeyError as e:
        raise KeyError(f"unknown harness kind: {kind}") from e


def get(
    kind: str,
    startup_model: str | None = None,
    *,
    startup_effort: str | None = None,
    version: str | None = None,
) -> HarnessAdapter:
    """Instantiate the adapter for *kind*.

    *version* is accepted now and threaded through for Phase 2 adapter
    dispatch (currently ignored — all kinds have a single adapter class).
    Raises KeyError if *kind* is unknown.
    """
    del version  # Phase 2: pass to resolve_adapter_id → select adapter class
    return REGISTRY[kind](startup_model=startup_model, startup_effort=startup_effort)


__all__ = [
    "CAPABILITY_REGISTRY",
    "CapabilityError",
    "HarnessAdapter",
    "HarnessCapabilities",
    "HarnessPaneState",
    "HarnessSession",
    "HarnessStartSpec",
    "REGISTRY",
    "SimpleResult",
    "capabilities_for",
    "get",
    "require",
]
