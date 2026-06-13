"""Pure, stateless agent-id helpers.

Lives in its own module (no DB/orchestrator imports) so renderer-agnostic
clients — the TUI today, the Ink frontend tomorrow — can reuse the convention
checks without pulling in the heavy orchestrator module.
"""

from __future__ import annotations

import re

# Rogue ids are built as ``f"{prefix}-rogue-{slug}"`` where ``prefix`` is a
# lowercase-alnum harness label (e.g. "claude", "codex"). Anchor on that
# structural shape so a stray "rogue-" substring inside a slug or some other
# id can't misclassify a non-rogue agent into the rogue routing path.
_ROGUE_ID_RE = re.compile(r"^[a-z0-9]+-rogue-")


def is_rogue_agent_id(agent_id: str) -> bool:
    """True for any rogue agent id regardless of harness prefix."""
    return _ROGUE_ID_RE.match(agent_id) is not None


__all__ = ["is_rogue_agent_id"]
