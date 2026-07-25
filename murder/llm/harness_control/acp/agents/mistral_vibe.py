"""Mistral Vibe ACP agent profile.

Onboarded as a single file: define ``PROFILE`` and register it.
Mistral Vibe exposes an ACP v1 agent server via ``vibe-acp`` (JSON-RPC
over stdio). Auth uses ``browser-auth`` (Mistral AI Studio); optional
``vibe-setup`` / ``MISTRAL_API_KEY`` are alternatives outside this profile.
Vendor extensions include auth/trust/session helpers (blocking) and
``_telemetry/send`` (notification). Murder keeps client fs/terminal off
so Vibe uses local tools.
See :mod:`murder.llm.harness_control.acp.agents` for the registration pattern.
"""

from __future__ import annotations

from murder.llm.harness_control.acp.agents.base import AcpAgentProfile

PROFILE = AcpAgentProfile(
    agent_id="mistral_vibe",
    harness_kind="mistral_vibe",
    argv=("vibe-acp",),
    auth_method_id="browser-auth",
    client_capabilities={
        "fs": {"readTextFile": False, "writeTextFile": False},
        "terminal": False,
    },
    placeholder_cmd=(
        "bash",
        "-lc",
        "printf 'murder: mistral_vibe acp\\n'; exec sleep infinity",
    ),
    blocking_extension_methods=frozenset(
        {
            "config/schema",
            "auth/status",
            "auth/signOut",
            "session/set_title",
            "session/delete",
            "trust/status",
            "trust/decision",
            "rewind/preview",
            "rewind/to",
        }
    ),
    notification_extension_methods=frozenset(
        {
            "_telemetry/send",
        }
    ),
)

__all__ = ["PROFILE"]
