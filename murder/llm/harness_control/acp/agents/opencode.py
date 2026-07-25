"""OpenCode ACP agent profile.

Onboarded as a single file: define ``PROFILE`` and register it.
OpenCode exposes an ACP v1 agent via ``opencode acp`` (JSON-RPC over stdio).
Auth uses ``opencode-login`` (out-of-band ``opencode auth login`` / terminal-auth).
OpenCode owns fs/terminal execution internally; optional ``opencode/question``
is a blocking extension when ``OPENCODE_ENABLE_QUESTION_TOOL=1``.
See :mod:`murder.llm.harness_control.acp.agents` for the registration pattern.
"""

from __future__ import annotations

from murder.llm.harness_control.acp.agents.base import AcpAgentProfile

PROFILE = AcpAgentProfile(
    agent_id="opencode",
    harness_kind="opencode",
    argv=("opencode", "acp"),
    auth_method_id="opencode-login",
    client_capabilities={
        "fs": {"readTextFile": False, "writeTextFile": False},
        "terminal": False,
    },
    placeholder_cmd=(
        "bash",
        "-lc",
        "printf 'murder: opencode acp\\n'; exec sleep infinity",
    ),
    blocking_extension_methods=frozenset({"opencode/question"}),
    notification_extension_methods=frozenset(),
)

__all__ = ["PROFILE"]
