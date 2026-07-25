"""Qwen Code ACP agent profile.

Onboarded as a single file: define ``PROFILE`` and register it.
See :mod:`murder.llm.harness_control.acp.agents` for the registration pattern.

Spawn: ``qwen --acp`` (JSON-RPC 2.0 over stdio). Auth via ``qwen-oauth``
(OAuth device flow); agent streams ``authenticate/update`` with the auth URI.
FS/terminal client capabilities are off — Qwen falls back to local FS and
built-in shell tools. Docs: https://qwenlm.github.io/qwen-code-docs/
"""

from __future__ import annotations

from murder.llm.harness_control.acp.agents.base import AcpAgentProfile

PROFILE = AcpAgentProfile(
    agent_id="qwen_code",
    harness_kind="qwen_code",
    argv=("qwen", "--acp"),
    auth_method_id="qwen-oauth",
    client_capabilities={
        "fs": {"readTextFile": False, "writeTextFile": False},
        "terminal": False,
    },
    placeholder_cmd=(
        "bash",
        "-lc",
        "printf 'murder: qwen_code acp\\n'; exec sleep infinity",
    ),
    blocking_extension_methods=frozenset(),
    notification_extension_methods=frozenset(
        {
            "authenticate/update",
        }
    ),
)

__all__ = ["PROFILE"]
