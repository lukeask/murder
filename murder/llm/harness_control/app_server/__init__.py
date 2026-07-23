"""Codex app-server JSON-RPC client (stdio process + helpers).

Public surface for later workstreams (W3–W5):

- :class:`AppServerConnection` — ``request`` / ``notify`` / ``respond`` /
  ``start`` / ``close``, plus ``notifications``, ``incoming_requests``,
  ``thread_id``, ``staged_composer_text``, ``current_turn_id``,
  ``desired_model``, ``desired_effort``
- :class:`AppServerClient` — initialize handshake and thread/turn helpers
- :mod:`~murder.llm.harness_control.app_server.protocol` — encode/decode
- :mod:`~murder.llm.harness_control.app_server.state` — view-state application
- :mod:`~murder.llm.harness_control.app_server.bootstrap` — process + thread start
"""

from __future__ import annotations

from murder.llm.harness_control.app_server.bootstrap import (
    APP_SERVER_PLACEHOLDER_CMD,
    start_app_server_session,
    uses_codex_app_server_backend,
)
from murder.llm.harness_control.app_server.client import (
    APPROVAL_ACCEPT,
    APPROVAL_ACCEPT_FOR_SESSION,
    APPROVAL_CANCEL,
    APPROVAL_DECLINE,
    DEFAULT_CLIENT_VERSION,
    AppServerClient,
    text_user_input,
)
from murder.llm.harness_control.app_server.connection import (
    DEFAULT_ARGV,
    AppServerConnection,
    AppServerRpcError,
    AppServerTransport,
)
from murder.llm.harness_control.app_server.protocol import (
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
from murder.llm.harness_control.app_server.state import (
    AppServerViewState,
    apply_notification,
    apply_server_request,
    remove_pending_request,
    to_snapshot_dict,
)

__all__ = [
    "APPROVAL_ACCEPT",
    "APPROVAL_ACCEPT_FOR_SESSION",
    "APPROVAL_CANCEL",
    "APPROVAL_DECLINE",
    "APP_SERVER_PLACEHOLDER_CMD",
    "DEFAULT_ARGV",
    "DEFAULT_CLIENT_VERSION",
    "AppServerClient",
    "AppServerConnection",
    "AppServerRpcError",
    "AppServerTransport",
    "AppServerViewState",
    "Params",
    "RequestId",
    "RpcError",
    "RpcMessage",
    "RpcNotification",
    "RpcRequest",
    "RpcResponse",
    "apply_notification",
    "apply_server_request",
    "decode_line",
    "decode_object",
    "encode_message",
    "is_error_response",
    "is_notification",
    "is_request",
    "is_response",
    "message_kind",
    "remove_pending_request",
    "start_app_server_session",
    "text_user_input",
    "to_snapshot_dict",
    "uses_codex_app_server_backend",
]
