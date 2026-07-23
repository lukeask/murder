"""Claude Agent SDK client (Python SDK process + helpers).

Public surface mirroring :mod:`murder.llm.harness_control.app_server`:

- :class:`AgentSdkConnection` — ``query`` / ``interrupt`` / ``respond_permission`` /
  ``start`` / ``aclose``, plus ``messages``, ``incoming_requests``,
  ``session_id``, ``staged_composer_text``, ``desired_model``, ``desired_effort``,
  ``prompt_in_flight``
- :class:`AgentSdkClient` — thin query/permission helpers
- :mod:`~murder.llm.harness_control.agent_sdk.state` — view-state application
- :mod:`~murder.llm.harness_control.agent_sdk.bootstrap` — process + session start
"""

from __future__ import annotations

from murder.llm.harness_control.agent_sdk.bootstrap import (
    AGENT_SDK_PLACEHOLDER_CMD,
    start_agent_sdk_session,
    uses_claude_agent_sdk_backend,
)
from murder.llm.harness_control.agent_sdk.client import (
    PERMISSION_ALLOW,
    PERMISSION_DENY,
    AgentSdkClient,
)
from murder.llm.harness_control.agent_sdk.connection import (
    DEFAULT_REQUEST_TIMEOUT_S,
    AgentSdkClientPort,
    AgentSdkConnection,
    AgentSdkError,
    normalize_sdk_message,
)
from murder.llm.harness_control.agent_sdk.state import (
    AgentSdkViewState,
    apply_event,
    apply_permission_request,
    remove_pending_request,
    to_snapshot_dict,
)

__all__ = [
    "AGENT_SDK_PLACEHOLDER_CMD",
    "DEFAULT_REQUEST_TIMEOUT_S",
    "PERMISSION_ALLOW",
    "PERMISSION_DENY",
    "AgentSdkClient",
    "AgentSdkClientPort",
    "AgentSdkConnection",
    "AgentSdkError",
    "AgentSdkViewState",
    "apply_event",
    "apply_permission_request",
    "normalize_sdk_message",
    "remove_pending_request",
    "start_agent_sdk_session",
    "to_snapshot_dict",
    "uses_claude_agent_sdk_backend",
]
