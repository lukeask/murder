"""High-level helpers over :class:`AcpConnection`.

Hand-types the ACP v1 surface Murder calls. Params use the camelCase field
names from the ACP schema (``sessionId``, ``methodId``, ``clientInfo``, …).
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from murder.llm.harness_control.acp.connection import AcpConnection
from murder.llm.harness_control.acp.protocol import RequestId

try:
    DEFAULT_CLIENT_VERSION = version("murder")
except PackageNotFoundError:  # pragma: no cover — editable/dev fallback
    DEFAULT_CLIENT_VERSION = "0.0.1"

# ACP protocolVersion for v1 (stable).
ACP_PROTOCOL_VERSION = 1

# Common permission option ids (session/request_permission responses).
PERMISSION_ALLOW_ONCE = "allow-once"
PERMISSION_ALLOW_ALWAYS = "allow-always"
PERMISSION_REJECT_ONCE = "reject-once"

DEFAULT_CLIENT_CAPABILITIES: dict[str, Any] = {
    "fs": {"readTextFile": False, "writeTextFile": False},
    "terminal": False,
}


def text_prompt_block(text: str) -> dict[str, str]:
    """Build a text content block for ``session/prompt``."""
    return {"type": "text", "text": text}


def permission_selected(option_id: str) -> dict[str, Any]:
    """Build a selected permission outcome for ``session/request_permission``."""
    return {"outcome": {"outcome": "selected", "optionId": option_id}}


def permission_cancelled() -> dict[str, Any]:
    """Build a cancelled permission outcome."""
    return {"outcome": {"outcome": "cancelled"}}


class AcpClient:
    """Thin RPC helpers for initialize / authenticate / session lifecycle."""

    def __init__(self, connection: AcpConnection) -> None:
        self.connection = connection

    async def initialize(
        self,
        *,
        protocol_version: int = ACP_PROTOCOL_VERSION,
        client_name: str = "murder",
        client_version: str | None = None,
        client_title: str | None = None,
        client_capabilities: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send ACP ``initialize`` (protocolVersion 1 + clientInfo/capabilities)."""
        client_info: dict[str, Any] = {
            "name": client_name,
            "version": DEFAULT_CLIENT_VERSION if client_version is None else client_version,
        }
        if client_title is not None:
            client_info["title"] = client_title
        params: dict[str, Any] = {
            "protocolVersion": protocol_version,
            "clientCapabilities": (
                DEFAULT_CLIENT_CAPABILITIES if client_capabilities is None else client_capabilities
            ),
            "clientInfo": client_info,
        }
        result = await self.connection.request("initialize", params)
        if not isinstance(result, dict):
            raise TypeError(f"initialize result must be an object, got {type(result).__name__}")
        return result

    async def authenticate(self, method_id: str, **kwargs: Any) -> Any:
        """Send ``authenticate`` with ``methodId`` (e.g. ``cursor_login``)."""
        params = _omit_none({"methodId": method_id, **kwargs})
        return await self.connection.request("authenticate", params)

    async def session_new(
        self,
        *,
        cwd: str,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create a session via ``session/new``; stores ``session_id`` on the connection."""
        params = _omit_none(
            {
                "cwd": cwd,
                "mcpServers": [] if mcp_servers is None else mcp_servers,
                **kwargs,
            }
        )
        result = await self.connection.request("session/new", params)
        self._remember_session_id(result)
        if not isinstance(result, dict):
            raise TypeError(f"session/new result must be an object, got {type(result).__name__}")
        return result

    async def session_load(self, session_id: str, **kwargs: Any) -> dict[str, Any]:
        """Resume a session via ``session/load`` (v1); stores ``session_id``."""
        params = _omit_none({"sessionId": session_id, **kwargs})
        result = await self.connection.request("session/load", params)
        self.connection.session_id = session_id
        self._remember_session_id(result)
        if not isinstance(result, dict):
            raise TypeError(f"session/load result must be an object, got {type(result).__name__}")
        return result

    async def session_prompt(
        self,
        session_id: str,
        prompt: str | list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send ``session/prompt``. Sets ``prompt_in_flight`` for the request duration.

        ``prompt`` is either plain text or a list of content blocks. v1 response
        carries ``stopReason`` when the turn completes.
        """
        if isinstance(prompt, str):
            blocks: list[dict[str, Any]] = [text_prompt_block(prompt)]
        else:
            blocks = prompt
        params = _omit_none({"sessionId": session_id, "prompt": blocks, **kwargs})
        self.connection.prompt_in_flight = True
        try:
            result = await self.connection.request("session/prompt", params)
        finally:
            self.connection.prompt_in_flight = False
        if not isinstance(result, dict):
            raise TypeError(f"session/prompt result must be an object, got {type(result).__name__}")
        return result

    async def session_cancel(self, session_id: str) -> None:
        """Send ``session/cancel`` as a notification."""
        await self.connection.notify("session/cancel", {"sessionId": session_id})

    async def respond_permission(
        self,
        request_id: RequestId,
        option_id: str = PERMISSION_ALLOW_ONCE,
    ) -> None:
        """Reply to ``session/request_permission`` with a selected option."""
        await self.connection.respond(request_id, result=permission_selected(option_id))

    def _remember_session_id(self, result: Any) -> None:
        if not isinstance(result, dict):
            return
        session_id = result.get("sessionId")
        if isinstance(session_id, str) and session_id:
            self.connection.session_id = session_id


def _omit_none(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}
