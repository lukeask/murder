"""Gemini CLI ACP agent profile.

Onboarded as a single file: define ``PROFILE`` and register it.
Gemini CLI exposes an ACP v1 agent server via ``gemini --acp`` (JSON-RPC
over stdio). Headless auth uses ``USE_GEMINI`` (API key); alternatives
documented by the agent include ``LOGIN_WITH_GOOGLE``, ``USE_VERTEX_AI``,
and ``GATEWAY``. Vendor extension: ``unstable_setSessionModel`` (blocking).
See :mod:`murder.llm.harness_control.acp.agents` for the registration pattern.
"""

from __future__ import annotations

from murder.llm.harness_control.acp.agents.base import AcpAgentProfile

PROFILE = AcpAgentProfile(
    agent_id="gemini_cli",
    harness_kind="gemini_cli",
    argv=("gemini", "--acp"),
    auth_method_id="USE_GEMINI",
    client_capabilities={
        "fs": {"readTextFile": False, "writeTextFile": False},
        "terminal": False,
    },
    placeholder_cmd=(
        "bash",
        "-lc",
        "printf 'murder: gemini_cli acp\\n'; exec sleep infinity",
    ),
    blocking_extension_methods=frozenset({"unstable_setSessionModel"}),
    notification_extension_methods=frozenset(),
)

__all__ = ["PROFILE"]
