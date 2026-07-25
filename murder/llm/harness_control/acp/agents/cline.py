"""Cline ACP agent profile.

Onboarded as a single file: define ``PROFILE`` and register it.
Cline exposes an ACP v1 agent server via ``cline --acp`` (JSON-RPC over
stdio). Auth uses ``methodId='cline'`` on main; shipping CLI does not
delegate to client fs/terminal. Documented vendor extension:
``unstable_setSessionModel`` (blocking).
See :mod:`murder.llm.harness_control.acp.agents` for the registration pattern.
"""

from __future__ import annotations

from murder.llm.harness_control.acp.agents.base import AcpAgentProfile

PROFILE = AcpAgentProfile(
    agent_id="cline",
    harness_kind="cline",
    argv=("cline", "--acp"),
    auth_method_id="cline",
    client_capabilities={
        "fs": {"readTextFile": False, "writeTextFile": False},
        "terminal": False,
    },
    placeholder_cmd=(
        "bash",
        "-lc",
        "printf 'murder: cline acp\\n'; exec sleep infinity",
    ),
    blocking_extension_methods=frozenset(
        {
            "unstable_setSessionModel",
        }
    ),
    notification_extension_methods=frozenset(),
)

__all__ = ["PROFILE"]
