"""Kimi Code CLI ACP agent profile.

Onboarded as a single file: define ``PROFILE`` and register it.
Kimi exposes a multi-session ACP v1 agent server via ``kimi acp`` (JSON-RPC
over stdio). Auth is terminal login (``method_id='login'``); no vendor
extension methods are documented — Murder uses the generic ACP adapter only.
See :mod:`murder.llm.harness_control.acp.agents` for the registration pattern.
"""

from __future__ import annotations

from murder.llm.harness_control.acp.agents.base import AcpAgentProfile

PROFILE = AcpAgentProfile(
    agent_id="kimi",
    harness_kind="kimi",
    argv=("kimi", "acp"),
    auth_method_id="login",
    client_capabilities={
        "fs": {"readTextFile": False, "writeTextFile": False},
        "terminal": False,
    },
    placeholder_cmd=(
        "bash",
        "-lc",
        "printf 'murder: kimi acp\\n'; exec sleep infinity",
    ),
    blocking_extension_methods=frozenset(),
    notification_extension_methods=frozenset(),
)

__all__ = ["PROFILE"]
