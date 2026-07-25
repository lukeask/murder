"""Factory Droid ACP agent profile.

Onboarded as a single file: define ``PROFILE`` and register it.
Factory Droid speaks ACP v1 natively via ``droid exec --output-format
acp-daemon`` (registry path; ``acp`` is the docs variant). Auth uses
``factory-api-key`` (``FACTORY_API_KEY``) for headless use; ``device-pairing``
is the interactive alternative. No ``factory/*`` blocking extension methods
are documented — empty frozensets.
See :mod:`murder.llm.harness_control.acp.agents` for the registration pattern.
"""

from __future__ import annotations

from murder.llm.harness_control.acp.agents.base import AcpAgentProfile

PROFILE = AcpAgentProfile(
    agent_id="factory_droid",
    harness_kind="factory_droid",
    argv=("droid", "exec", "--output-format", "acp-daemon"),
    auth_method_id="factory-api-key",
    client_capabilities={
        "fs": {"readTextFile": False, "writeTextFile": False},
        "terminal": False,
    },
    placeholder_cmd=(
        "bash",
        "-lc",
        "printf 'murder: factory droid acp\\n'; exec sleep infinity",
    ),
    blocking_extension_methods=frozenset(),
    notification_extension_methods=frozenset(),
)

__all__ = ["PROFILE"]
