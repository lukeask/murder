"""Pure, stateless agent-id helpers.

Lives in its own module (no DB/orchestrator imports) so renderer-agnostic
clients — the TUI today, the Ink frontend tomorrow — can reuse the convention
checks without pulling in the heavy orchestrator module.
"""

from __future__ import annotations


def is_rogue_agent_id(agent_id: str) -> bool:
    """True for any rogue agent id regardless of harness prefix."""
    return "rogue-" in agent_id


__all__ = ["is_rogue_agent_id"]
