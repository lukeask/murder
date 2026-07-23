"""ACP agent registry.

To onboard a new ACP harness:

1. Add ``agents/<name>.py`` defining ``PROFILE = AcpAgentProfile(...)``.
2. Import that module below so ``register_agent(PROFILE)`` runs at import time.
3. Call ``register_agent`` from the new module (or do it here after import).

Lookup helpers: :func:`get_agent`, :func:`list_agents`, :func:`get_agent_for_harness`.
"""

from __future__ import annotations

from murder.llm.harness_control.acp.agents.base import AcpAgentProfile

# Known agent modules — import each PROFILE so registration can run below.
# **To onboard a new ACP harness, add ``agents/<name>.py`` with a PROFILE
# and import it here.**
from murder.llm.harness_control.acp.agents.cursor import PROFILE as _CURSOR_PROFILE

_REGISTRY: dict[str, AcpAgentProfile] = {}
_BY_HARNESS: dict[str, AcpAgentProfile] = {}


def register_agent(profile: AcpAgentProfile) -> AcpAgentProfile:
    """Register (or replace) an ACP agent profile. Returns the profile."""
    _REGISTRY[profile.agent_id] = profile
    _BY_HARNESS[profile.harness_kind] = profile
    return profile


def get_agent(agent_id: str) -> AcpAgentProfile:
    """Return a registered profile by ``agent_id``, or raise ``KeyError``."""
    try:
        return _REGISTRY[agent_id]
    except KeyError as exc:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise KeyError(f"unknown ACP agent {agent_id!r}; known: {known}") from exc


def get_agent_for_harness(harness_kind: str) -> AcpAgentProfile | None:
    """Return the ACP profile for a Murder harness kind, or ``None``."""
    return _BY_HARNESS.get(harness_kind)


def list_agents() -> list[AcpAgentProfile]:
    """Return all registered profiles sorted by ``agent_id``."""
    return [profile for _, profile in sorted(_REGISTRY.items())]


register_agent(_CURSOR_PROFILE)

__all__ = [
    "AcpAgentProfile",
    "get_agent",
    "get_agent_for_harness",
    "list_agents",
    "register_agent",
]
