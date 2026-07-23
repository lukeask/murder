"""High-level helpers over :class:`AppServerConnection`.

Hand-types only the small surface Murder calls. Params use the camelCase
field names from the generated Codex JSON schemas.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from murder.llm.harness_control.app_server.connection import AppServerConnection
from murder.llm.harness_control.app_server.protocol import RequestId

try:
    DEFAULT_CLIENT_VERSION = version("murder")
except PackageNotFoundError:  # pragma: no cover — editable/dev fallback
    DEFAULT_CLIENT_VERSION = "0.0.1"

# Common approval decision strings (command/file-change requestApproval responses).
APPROVAL_ACCEPT = "accept"
APPROVAL_ACCEPT_FOR_SESSION = "acceptForSession"
APPROVAL_DECLINE = "decline"
APPROVAL_CANCEL = "cancel"


def text_user_input(text: str) -> dict[str, str]:
    """Build a ``UserInput`` text item for ``turn/start``."""
    return {"type": "text", "text": text}


class AppServerClient:
    """Thin RPC helpers for initialize / thread / turn / approval replies."""

    def __init__(self, connection: AppServerConnection) -> None:
        self.connection = connection

    async def initialize(
        self,
        *,
        client_name: str = "murder",
        client_version: str | None = None,
        client_title: str | None = None,
        capabilities: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send ``initialize``, then the ``initialized`` notification.

        Initialize response shape (schema): ``userAgent``, ``codexHome``,
        ``platformOs``, ``platformFamily``.
        """
        client_info: dict[str, Any] = {
            "name": client_name,
            "version": DEFAULT_CLIENT_VERSION if client_version is None else client_version,
        }
        if client_title is not None:
            client_info["title"] = client_title
        params: dict[str, Any] = {"clientInfo": client_info}
        if capabilities is not None:
            params["capabilities"] = capabilities
        result = await self.connection.request("initialize", params)
        await self.connection.notify("initialized")
        if not isinstance(result, dict):
            raise TypeError(f"initialize result must be an object, got {type(result).__name__}")
        return result

    async def thread_start(
        self,
        *,
        cwd: str | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        params = _omit_none({"cwd": cwd, "model": model, **kwargs})
        result = await self.connection.request("thread/start", params or {})
        self._remember_thread_id(result)
        if not isinstance(result, dict):
            raise TypeError(f"thread/start result must be an object, got {type(result).__name__}")
        return result

    async def thread_resume(self, thread_id: str, **kwargs: Any) -> dict[str, Any]:
        params = _omit_none({"threadId": thread_id, **kwargs})
        result = await self.connection.request("thread/resume", params)
        self.connection.thread_id = thread_id
        self._remember_thread_id(result)
        if not isinstance(result, dict):
            raise TypeError(f"thread/resume result must be an object, got {type(result).__name__}")
        return result

    async def turn_start(
        self,
        thread_id: str,
        input_text: str | list[dict[str, Any]],
        *,
        model: str | None = None,
        effort: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Start a turn. ``input`` is either plain text or a UserInput list."""
        if isinstance(input_text, str):
            user_input: list[dict[str, Any]] = [text_user_input(input_text)]
        else:
            user_input = input_text
        params = _omit_none(
            {
                "threadId": thread_id,
                "input": user_input,
                "model": model,
                "effort": effort,
                **kwargs,
            }
        )
        result = await self.connection.request("turn/start", params)
        if not isinstance(result, dict):
            raise TypeError(f"turn/start result must be an object, got {type(result).__name__}")
        return result

    async def turn_interrupt(self, thread_id: str, turn_id: str) -> Any:
        """Interrupt an in-flight turn.

        Schema ``TurnInterruptParams`` requires both ``threadId`` and ``turnId``.
        """
        return await self.connection.request(
            "turn/interrupt",
            {"threadId": thread_id, "turnId": turn_id},
        )

    async def respond_approval(
        self,
        request_id: RequestId,
        decision: str = APPROVAL_ACCEPT,
        **extra: Any,
    ) -> None:
        """Reply to a server approval request with ``{decision, ...}``."""
        result: dict[str, Any] = {"decision": decision, **extra}
        await self.connection.respond(request_id, result=result)

    def _remember_thread_id(self, result: Any) -> None:
        if not isinstance(result, dict):
            return
        thread = result.get("thread")
        if isinstance(thread, dict):
            thread_id = thread.get("id")
            if isinstance(thread_id, str) and thread_id:
                self.connection.thread_id = thread_id


def _omit_none(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}
