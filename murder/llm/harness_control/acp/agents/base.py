"""ACP agent profile definition.

Each onboarded agent is a frozen :class:`AcpAgentProfile` that holds everything
needed to spawn and talk to that agent — no Cursor (or other) specifics belong
in the core connection/client modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AcpAgentProfile:
    """Everything needed to spawn/talk to one ACP agent without hardcoding it.

    Attributes:
        agent_id: Registry key (e.g. ``\"cursor\"``).
        harness_kind: Murder harness kind this profile backs (e.g. ``\"cursor\"``).
        argv: Process argv to spawn the agent ACP server (e.g. ``(\"agent\", \"acp\")``).
        auth_method_id: ACP ``authenticate`` methodId, or ``None`` to skip auth.
        client_capabilities: Default ``clientCapabilities`` for ``initialize``.
        placeholder_cmd: Optional tmux placeholder pane command when using ACP backend.
        blocking_extension_methods: Agent→client extension methods that require a reply.
        notification_extension_methods: Fire-and-forget extension notifications.
    """

    agent_id: str
    harness_kind: str
    argv: tuple[str, ...]
    auth_method_id: str | None = None
    client_capabilities: dict[str, Any] = field(
        default_factory=lambda: {
            "fs": {"readTextFile": False, "writeTextFile": False},
            "terminal": False,
        }
    )
    placeholder_cmd: tuple[str, ...] | None = None
    blocking_extension_methods: frozenset[str] = frozenset()
    notification_extension_methods: frozenset[str] = frozenset()


__all__ = ["AcpAgentProfile"]
