"""Goose ACP agent profile.

Onboarded as a single file: define ``PROFILE`` and register it.
Goose exposes an ACP v1 agent server via ``goose acp`` (JSON-RPC over
stdio). Auth advertises ``goose-provider`` (pointing users to
``goose configure``); the authenticate handler is a stub. Murder does
not enumerate Goose's ``_goose/unstable/*`` extension methods — the
generic ACP adapter handles standard methods only.
See :mod:`murder.llm.harness_control.acp.agents` for the registration pattern.
"""

from __future__ import annotations

from murder.llm.harness_control.acp.agents.base import AcpAgentProfile

PROFILE = AcpAgentProfile(
    agent_id="goose",
    harness_kind="goose",
    argv=("goose", "acp"),
    auth_method_id="goose-provider",
    client_capabilities={
        "fs": {"readTextFile": False, "writeTextFile": False},
        "terminal": False,
    },
    placeholder_cmd=(
        "bash",
        "-lc",
        "printf 'murder: goose acp\\n'; exec sleep infinity",
    ),
    blocking_extension_methods=frozenset(),
    notification_extension_methods=frozenset(),
)

__all__ = ["PROFILE"]
