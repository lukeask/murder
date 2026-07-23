"""Claude Agent SDK session connection.

Owns a ``ClaudeSDKClient`` (or an injected test double), a message reader task,
and futures for ``can_use_tool`` permission / AskUserQuestion prompts.
Connection-local staged state mirrors the app-server surface
(``session_id``, ``staged_composer_text``, ``desired_model``, ``desired_effort``,
``prompt_in_flight``).
"""

# ruff: noqa: PLC0415, PLR0911, PLR0912

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import uuid4

logger = logging.getLogger(__name__)

DEFAULT_REQUEST_TIMEOUT_S = 60.0


class AgentSdkError(RuntimeError):
    """Raised for Agent SDK connection / client failures."""


class AgentSdkClientPort(Protocol):
    """Minimal ClaudeSDKClient surface used by :class:`AgentSdkConnection`."""

    async def connect(self, prompt: str | AsyncIterator[dict[str, Any]] | None = None) -> None: ...

    async def disconnect(self) -> None: ...

    async def query(self, prompt: str, session_id: str = "default") -> None: ...

    async def interrupt(self) -> None: ...

    async def set_model(self, model: str | None = None) -> None: ...

    def receive_messages(self) -> AsyncIterator[Any]: ...


@dataclass
class _PendingPermission:
    future: asyncio.Future[Any]
    tool_name: str
    input_data: dict[str, Any]
    context: Any = None


@dataclass
class AgentSdkConnection:
    """Bidirectional Claude Agent SDK session with Murder-facing queues."""

    cwd: str | None = None
    model: str | None = None
    effort: str | None = None
    env: Mapping[str, str] | None = None
    cli_path: str | None = None
    permission_mode: str = "default"
    include_partial_messages: bool = True
    client: AgentSdkClientPort | None = None
    request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S

    messages: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    incoming_requests: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)

    session_id: str | None = None
    staged_composer_text: str = ""
    desired_model: str | None = None
    desired_effort: str | None = None
    prompt_in_flight: bool = False

    _started: bool = field(default=False, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)
    _reader_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _pending_permissions: dict[str, _PendingPermission] = field(
        default_factory=dict, init=False, repr=False
    )
    _owns_client: bool = field(default=False, init=False, repr=False)

    @property
    def started(self) -> bool:
        return self._started and not self._closed

    async def start(self) -> None:
        if self._started:
            return
        if self._closed:
            raise RuntimeError("AgentSdkConnection is closed")
        if self.client is None:
            self.client = self._build_real_client()
            self._owns_client = True
        if self.desired_model is None and self.model is not None:
            self.desired_model = self.model
        if self.desired_effort is None and self.effort is not None:
            self.desired_effort = self.effort
        await self.client.connect()
        self._reader_task = asyncio.create_task(self._read_loop(), name="agent-sdk-reader")
        self._started = True

    async def query(self, prompt: str) -> None:
        self._ensure_started()
        assert self.client is not None
        self.prompt_in_flight = True
        await self.client.query(prompt)

    async def interrupt(self) -> None:
        self._ensure_started()
        assert self.client is not None
        await self.client.interrupt()

    async def set_model(self, model: str | None) -> None:
        self._ensure_started()
        assert self.client is not None
        self.desired_model = model
        await self.client.set_model(model)

    async def respond_permission(
        self,
        request_id: str,
        *,
        behavior: str,
        updated_input: dict[str, Any] | None = None,
        message: str = "",
        interrupt: bool = False,
    ) -> None:
        """Resolve a pending ``can_use_tool`` future."""

        pending = self._pending_permissions.pop(request_id, None)
        if pending is None:
            raise AgentSdkError(f"no pending permission request id={request_id!r}")
        merged_input = dict(pending.input_data)
        if updated_input is not None:
            merged_input.update(updated_input)
        result = self._permission_result(
            behavior=behavior,
            updated_input=merged_input,
            message=message,
            interrupt=interrupt,
        )
        if not pending.future.done():
            pending.future.set_result(result)

    def drain_messages(self) -> list[dict[str, Any]]:
        drained: list[dict[str, Any]] = []
        while True:
            try:
                drained.append(self.messages.get_nowait())
            except asyncio.QueueEmpty:
                return drained

    def drain_incoming_requests(self) -> list[dict[str, Any]]:
        drained: list[dict[str, Any]] = []
        while True:
            try:
                drained.append(self.incoming_requests.get_nowait())
            except asyncio.QueueEmpty:
                return drained

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        for pending in list(self._pending_permissions.values()):
            if not pending.future.done():
                pending.future.set_exception(AgentSdkError("Agent SDK connection closed"))
        self._pending_permissions.clear()
        if self.client is not None and self._owns_client:
            try:
                await self.client.disconnect()
            except Exception:  # noqa: BLE001 — shutdown must continue
                logger.debug("Agent SDK client disconnect failed", exc_info=True)
        self._started = False

    async def __aenter__(self) -> AgentSdkConnection:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    def _ensure_started(self) -> None:
        if not self.started:
            raise RuntimeError("AgentSdkConnection is not started")

    def _build_real_client(self) -> AgentSdkClientPort:
        try:
            from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
            from claude_agent_sdk.types import (
                PermissionResultAllow,
                PermissionResultDeny,
            )
        except ImportError as exc:  # pragma: no cover — exercised when package absent
            raise ImportError(
                "claude-agent-sdk is required for the Claude Agent SDK control backend; "
                "install with: pip install 'murder[agent_sdk]' or pip install claude-agent-sdk"
            ) from exc

        # Keep references for can_use_tool result construction without re-importing.
        self._PermissionResultAllow = PermissionResultAllow  # type: ignore[attr-defined]
        self._PermissionResultDeny = PermissionResultDeny  # type: ignore[attr-defined]

        options_kwargs: dict[str, Any] = {
            "permission_mode": self.permission_mode,
            "can_use_tool": self._can_use_tool,
            "include_partial_messages": self.include_partial_messages,
            "cwd": self.cwd,
        }
        if self.desired_model or self.model:
            options_kwargs["model"] = self.desired_model or self.model
        if self.desired_effort or self.effort:
            options_kwargs["effort"] = self.desired_effort or self.effort
        if self.env is not None:
            options_kwargs["env"] = dict(self.env)
        if self.cli_path is not None:
            options_kwargs["cli_path"] = self.cli_path

        options = ClaudeAgentOptions(**options_kwargs)
        return ClaudeSDKClient(options=options)

    async def _can_use_tool(
        self, tool_name: str, input_data: dict[str, Any], context: Any
    ) -> Any:
        request_id = None
        if context is not None:
            request_id = getattr(context, "tool_use_id", None)
        if not isinstance(request_id, str) or not request_id:
            request_id = str(uuid4())
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending_permissions[request_id] = _PendingPermission(
            future=future,
            tool_name=tool_name,
            input_data=dict(input_data),
            context=context,
        )
        title = getattr(context, "title", None) if context is not None else None
        description = getattr(context, "description", None) if context is not None else None
        await self.incoming_requests.put(
            {
                "id": request_id,
                "tool_name": tool_name,
                "input": dict(input_data),
                "title": title,
                "description": description,
            }
        )
        return await asyncio.wait_for(future, timeout=None)

    def _permission_result(
        self,
        *,
        behavior: str,
        updated_input: dict[str, Any] | None,
        message: str,
        interrupt: bool,
    ) -> Any:
        allow_cls = getattr(self, "_PermissionResultAllow", None)
        deny_cls = getattr(self, "_PermissionResultDeny", None)
        if behavior in {"allow", "accept", "acceptForSession", "allow_once", "allow_always"}:
            if allow_cls is not None:
                return allow_cls(updated_input=updated_input)
            return {"behavior": "allow", "updated_input": updated_input}
        if deny_cls is not None:
            return deny_cls(message=message or "User denied this action", interrupt=interrupt)
        return {
            "behavior": "deny",
            "message": message or "User denied this action",
            "interrupt": interrupt,
        }

    async def _read_loop(self) -> None:
        assert self.client is not None
        try:
            async for raw in self.client.receive_messages():
                event = normalize_sdk_message(raw)
                if event is None:
                    continue
                if event.get("kind") == "result":
                    self.prompt_in_flight = False
                    session_id = event.get("session_id")
                    if isinstance(session_id, str) and session_id:
                        self.session_id = session_id
                await self.messages.put(event)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.debug("Agent SDK reader exited with error", exc_info=True)
            self.prompt_in_flight = False


def normalize_sdk_message(raw: Any) -> dict[str, Any] | None:
    """Convert a Claude Agent SDK message object into an internal event dict."""

    type_name = type(raw).__name__
    if isinstance(raw, dict) and "kind" in raw:
        return raw

    if type_name == "UserMessage" or (
        isinstance(raw, dict) and raw.get("type") in {"user", "UserMessage"}
    ):
        content = getattr(raw, "content", None) if not isinstance(raw, dict) else raw.get("content")
        tool_use_id = (
            getattr(raw, "parent_tool_use_id", None)
            if not isinstance(raw, dict)
            else raw.get("parent_tool_use_id")
        )
        tool_result = (
            getattr(raw, "tool_use_result", None)
            if not isinstance(raw, dict)
            else raw.get("tool_use_result")
        )
        if tool_result is not None or tool_use_id:
            result_content = tool_result
            if isinstance(tool_result, dict):
                result_content = tool_result.get("content", tool_result)
            return {
                "kind": "tool_result",
                "tool_use_id": tool_use_id,
                "content": _content_to_text(result_content),
                "is_error": bool(
                    tool_result.get("is_error") if isinstance(tool_result, dict) else False
                ),
            }
        return {
            "kind": "user",
            "text": _content_to_text(content),
            "uuid": getattr(raw, "uuid", None) if not isinstance(raw, dict) else raw.get("uuid"),
        }

    if type_name == "AssistantMessage" or (
        isinstance(raw, dict) and raw.get("type") in {"assistant", "AssistantMessage"}
    ):
        content = getattr(raw, "content", None) if not isinstance(raw, dict) else raw.get("content")
        text_parts: list[str] = []
        tool_uses: list[dict[str, Any]] = []
        for block in content or []:
            block_name = type(block).__name__
            if block_name == "TextBlock" or (
                isinstance(block, dict) and block.get("type") == "text"
            ):
                text = (
                    getattr(block, "text", None)
                    if not isinstance(block, dict)
                    else block.get("text")
                )
                if isinstance(text, str):
                    text_parts.append(text)
            elif block_name == "ToolUseBlock" or (
                isinstance(block, dict) and block.get("type") == "tool_use"
            ):
                tool_uses.append(
                    {
                        "id": getattr(block, "id", None)
                        if not isinstance(block, dict)
                        else block.get("id"),
                        "name": getattr(block, "name", None)
                        if not isinstance(block, dict)
                        else block.get("name"),
                        "input": getattr(block, "input", None)
                        if not isinstance(block, dict)
                        else block.get("input"),
                    }
                )
        return {
            "kind": "assistant",
            "text": "".join(text_parts),
            "tool_uses": tool_uses,
            "model": getattr(raw, "model", None) if not isinstance(raw, dict) else raw.get("model"),
            "message_id": getattr(raw, "message_id", None)
            if not isinstance(raw, dict)
            else raw.get("message_id"),
            "uuid": getattr(raw, "uuid", None) if not isinstance(raw, dict) else raw.get("uuid"),
            "session_id": getattr(raw, "session_id", None)
            if not isinstance(raw, dict)
            else raw.get("session_id"),
            "partial": False,
        }

    if type_name == "ResultMessage" or (
        isinstance(raw, dict) and raw.get("type") in {"result", "ResultMessage"}
    ):
        get = (lambda key, default=None: getattr(raw, key, default)) if not isinstance(
            raw, dict
        ) else raw.get
        return {
            "kind": "result",
            "subtype": get("subtype"),
            "session_id": get("session_id"),
            "usage": get("usage"),
            "total_cost_usd": get("total_cost_usd"),
            "result": get("result"),
            "is_error": get("is_error"),
            "errors": get("errors"),
        }

    if type_name == "SystemMessage" or (
        isinstance(raw, dict) and raw.get("type") in {"system", "SystemMessage"}
    ):
        get = (lambda key, default=None: getattr(raw, key, default)) if not isinstance(
            raw, dict
        ) else raw.get
        return {"kind": "system", "subtype": get("subtype"), "data": get("data") or {}}

    if type_name == "StreamEvent" or (
        isinstance(raw, dict) and raw.get("type") in {"stream_event", "StreamEvent"}
    ):
        event = getattr(raw, "event", None) if not isinstance(raw, dict) else raw.get("event")
        if not isinstance(event, dict):
            return None
        if event.get("type") == "content_block_delta":
            delta = event.get("delta")
            text = None
            if isinstance(delta, dict) and delta.get("type") == "text_delta":
                text = delta.get("text")
            if isinstance(text, str) and text:
                return {
                    "kind": "stream_delta",
                    "item_id": str(event.get("index", "stream")),
                    "delta": text,
                }
        return None

    return None


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
            else:
                text = getattr(block, "text", None)
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content)


__all__ = [
    "AgentSdkClientPort",
    "AgentSdkConnection",
    "AgentSdkError",
    "DEFAULT_REQUEST_TIMEOUT_S",
    "normalize_sdk_message",
]
