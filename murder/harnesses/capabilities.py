"""Declared harness capability flags (registry is source of truth)."""

from __future__ import annotations

from dataclasses import dataclass


class CapabilityError(RuntimeError):
    """Raised when a harness does not declare support for an operation."""


@dataclass(frozen=True, slots=True)
class HarnessCapabilities:
    usage_reporting: bool = False
    model_discovery: bool = False
    model_selection: bool = False
    pane_state_reading: bool = False
    transcript_access: bool = False
    resumable_after_exhaustion: bool = False
    structured_output_reliable: bool = False
    startup_interrupt_continue: bool = False
    supports_subagents: bool = False
    cheapest_subagent_model: str | None = None


def require(capabilities: HarnessCapabilities, name: str) -> None:
    if not getattr(capabilities, name):
        raise CapabilityError(f"harness does not support {name!r}")


__all__ = ["HarnessCapabilities"]
