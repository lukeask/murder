"""GitHub Copilot CLI ACP agent profile.

Onboarded as a single file: define ``PROFILE`` and register it.
Copilot exposes a native ACP v1 agent server via ``copilot --acp --stdio``
(JSON-RPC over NDJSON). Auth is terminal login (``method_id='copilot-login'``);
no vendor extension methods are documented — Murder uses the generic ACP
adapter only. Copilot executes tools agent-side and does not call client
``fs/*`` or ``terminal/*``.
See :mod:`murder.llm.harness_control.acp.agents` for the registration pattern.
"""

from __future__ import annotations

from murder.llm.harness_control.acp.agents.base import AcpAgentProfile

PROFILE = AcpAgentProfile(
    agent_id="github_copilot",
    harness_kind="github_copilot",
    argv=("copilot", "--acp", "--stdio"),
    auth_method_id="copilot-login",
    client_capabilities={
        "fs": {"readTextFile": False, "writeTextFile": False},
        "terminal": False,
    },
    placeholder_cmd=(
        "bash",
        "-lc",
        "printf 'murder: github_copilot acp\\n'; exec sleep infinity",
    ),
    blocking_extension_methods=frozenset(),
    notification_extension_methods=frozenset(),
)

__all__ = ["PROFILE"]
