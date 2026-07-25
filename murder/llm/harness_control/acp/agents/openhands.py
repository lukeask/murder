"""OpenHands ACP agent profile.

Onboarded as a single file: define ``PROFILE`` and register it.
OpenHands exposes an ACP v1 agent server via ``openhands acp`` (JSON-RPC over
stdio). Auth uses pre-configured ``~/.openhands/settings.json`` credentials
with no documented ACP ``methodId``, so authenticate is skipped. Client
fs/terminal are disabled; OpenHands uses its own runtime when the client
lacks those capabilities. No vendor extension methods are documented.
See :mod:`murder.llm.harness_control.acp.agents` for the registration pattern.
"""

from __future__ import annotations

from murder.llm.harness_control.acp.agents.base import AcpAgentProfile

PROFILE = AcpAgentProfile(
    agent_id="openhands",
    harness_kind="openhands",
    argv=("openhands", "acp"),
    auth_method_id=None,
    client_capabilities={
        "fs": {"readTextFile": False, "writeTextFile": False},
        "terminal": False,
    },
    placeholder_cmd=(
        "bash",
        "-lc",
        "printf 'murder: openhands acp\\n'; exec sleep infinity",
    ),
    blocking_extension_methods=frozenset(),
    notification_extension_methods=frozenset(),
)

__all__ = ["PROFILE"]
