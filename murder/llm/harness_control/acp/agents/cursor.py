"""Cursor ACP agent profile.

Onboarded as a single file: define ``PROFILE`` and register it.
See :mod:`murder.llm.harness_control.acp.agents` for the registration pattern.
"""

from __future__ import annotations

from murder.llm.harness_control.acp.agents.base import AcpAgentProfile

PROFILE = AcpAgentProfile(
    agent_id="cursor",
    harness_kind="cursor",
    argv=("agent", "acp"),
    auth_method_id="cursor_login",
    client_capabilities={
        "fs": {"readTextFile": False, "writeTextFile": False},
        "terminal": False,
    },
    placeholder_cmd=(
        "bash",
        "-lc",
        "printf 'murder: cursor acp\\n'; exec sleep infinity",
    ),
    blocking_extension_methods=frozenset(
        {
            "cursor/ask_question",
            "cursor/create_plan",
        }
    ),
    notification_extension_methods=frozenset(
        {
            "cursor/update_todos",
            "cursor/task",
            "cursor/generate_image",
        }
    ),
)

__all__ = ["PROFILE"]
