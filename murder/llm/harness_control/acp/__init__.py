"""ACP (Agent Client Protocol) JSON-RPC client (stdio process + helpers).

Public surface:

- :class:`AcpConnection` — ``request`` / ``notify`` / ``respond`` /
  ``start`` / ``close``, plus ``notifications``, ``incoming_requests``,
  ``session_id``, ``staged_composer_text``, ``desired_model``,
  ``desired_effort``, ``prompt_in_flight``
- :class:`AcpClient` — initialize / authenticate / session helpers
- :mod:`~murder.llm.harness_control.acp.protocol` — encode/decode (with jsonrpc)
- :mod:`~murder.llm.harness_control.acp.state` — view-state application
- :mod:`~murder.llm.harness_control.acp.bootstrap` — process + session start
- :mod:`~murder.llm.harness_control.acp.agents` — modular agent registry

To onboard a new ACP harness, add ``agents/<name>.py`` with a PROFILE and
import it in ``agents/__init__.py``.
"""

from __future__ import annotations

from murder.llm.harness_control.acp.agents import (
    AcpAgentProfile,
    get_agent,
    get_agent_for_harness,
    list_agents,
    register_agent,
)
from murder.llm.harness_control.acp.bootstrap import (
    placeholder_cmd_for_profile,
    resolve_agent_profile,
    start_acp_session,
    uses_acp_backend,
)
from murder.llm.harness_control.acp.client import (
    ACP_PROTOCOL_VERSION,
    DEFAULT_CLIENT_CAPABILITIES,
    DEFAULT_CLIENT_VERSION,
    PERMISSION_ALLOW_ALWAYS,
    PERMISSION_ALLOW_ONCE,
    PERMISSION_REJECT_ONCE,
    AcpClient,
    permission_cancelled,
    permission_selected,
    text_prompt_block,
)
from murder.llm.harness_control.acp.connection import (
    AcpConnection,
    AcpRpcError,
    AcpTransport,
)
from murder.llm.harness_control.acp.protocol import (
    JSONRPC_VERSION,
    Params,
    RequestId,
    RpcError,
    RpcMessage,
    RpcNotification,
    RpcRequest,
    RpcResponse,
    decode_line,
    decode_object,
    encode_message,
    is_error_response,
    is_notification,
    is_request,
    is_response,
    message_kind,
)
from murder.llm.harness_control.acp.state import (
    AcpViewState,
    apply_notification,
    apply_server_request,
    apply_stop_reason,
    mark_prompt_started,
    remove_pending_request,
    to_snapshot_dict,
)

__all__ = [
    "ACP_PROTOCOL_VERSION",
    "DEFAULT_CLIENT_CAPABILITIES",
    "DEFAULT_CLIENT_VERSION",
    "JSONRPC_VERSION",
    "PERMISSION_ALLOW_ALWAYS",
    "PERMISSION_ALLOW_ONCE",
    "PERMISSION_REJECT_ONCE",
    "AcpAgentProfile",
    "AcpClient",
    "AcpConnection",
    "AcpRpcError",
    "AcpTransport",
    "AcpViewState",
    "Params",
    "RequestId",
    "RpcError",
    "RpcMessage",
    "RpcNotification",
    "RpcRequest",
    "RpcResponse",
    "apply_notification",
    "apply_server_request",
    "apply_stop_reason",
    "decode_line",
    "decode_object",
    "encode_message",
    "get_agent",
    "get_agent_for_harness",
    "is_error_response",
    "is_notification",
    "is_request",
    "is_response",
    "list_agents",
    "mark_prompt_started",
    "message_kind",
    "permission_cancelled",
    "permission_selected",
    "placeholder_cmd_for_profile",
    "register_agent",
    "remove_pending_request",
    "resolve_agent_profile",
    "start_acp_session",
    "text_prompt_block",
    "to_snapshot_dict",
    "uses_acp_backend",
]
