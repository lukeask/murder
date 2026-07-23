# ruff: noqa: PLR0911, PLR0912, PLR0915
"""Mutable ACP view state and notification application.

Accumulates ``session/update`` streaming into normalized transcript items and
pending agent→client requests for later frame snapshots / adapters.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Literal

from murder.llm.harness_control.acp.protocol import RpcNotification, RpcRequest

TurnStatus = Literal["idle", "streaming", "completed", "cancelled", "failed"]

_STOP_REASON_TO_STATUS: dict[str, TurnStatus] = {
    "end_turn": "completed",
    "max_tokens": "completed",
    "max_requests": "completed",
    "refusal": "failed",
    "cancelled": "cancelled",
}


@dataclass
class AcpViewState:
    """Accumulated ACP session/turn/item state for frame snapshots."""

    session_id: str | None = None
    turn_status: TurnStatus | None = None
    items: list[dict[str, Any]] = field(default_factory=list)
    pending_requests: list[dict[str, Any]] = field(default_factory=list)
    model_id: str | None = None
    effort: str | None = None
    usage: dict[str, Any] | None = None
    stop_reason: str | None = None
    current_mode: str | None = None
    # Streaming message chunk accumulators keyed by synthetic item id.
    _stream_message_id: str | None = field(default=None, repr=False)
    _stream_thought_id: str | None = field(default=None, repr=False)
    _stream_user_id: str | None = field(default=None, repr=False)


def mark_prompt_started(state: AcpViewState) -> None:
    """Caller hook: mark turn as streaming when ``session/prompt`` begins."""
    state.turn_status = "streaming"
    state.stop_reason = None
    state._stream_message_id = None
    state._stream_thought_id = None
    state._stream_user_id = None


def apply_stop_reason(state: AcpViewState, stop_reason: str | None) -> None:
    """Update turn status from a v1 ``session/prompt`` ``stopReason``."""
    if stop_reason is None:
        state.turn_status = "completed"
        return
    state.stop_reason = stop_reason
    state.turn_status = _STOP_REASON_TO_STATUS.get(stop_reason, "completed")


def apply_notification(state: AcpViewState, notification: RpcNotification) -> None:
    """Apply one agent notification to ``state``. Unknown methods are ignored."""

    method = notification.method
    params = notification.params if isinstance(notification.params, dict) else {}

    if method == "session/update":
        session_id = params.get("sessionId")
        if isinstance(session_id, str) and session_id:
            state.session_id = session_id
        update = params.get("update")
        if isinstance(update, dict):
            _apply_session_update(state, update)
        return

    # Cursor (and other) extension notifications — record lightly if useful later.
    # Unknown methods: ignore safely.


def apply_server_request(state: AcpViewState, request: RpcRequest) -> None:
    """Record an agent→client request (permissions / cursor extensions) as pending."""

    params = request.params if isinstance(request.params, dict) else {}
    if not isinstance(params, dict):
        params = {}
    state.pending_requests.append(
        {
            "id": request.id,
            "method": request.method,
            "params": copy.deepcopy(params),
        }
    )


def remove_pending_request(state: AcpViewState, request_id: str | int) -> None:
    """Drop a pending request by JSON-RPC id after it has been answered."""

    state.pending_requests = [
        entry for entry in state.pending_requests if entry.get("id") != request_id
    ]


def to_snapshot_dict(
    state: AcpViewState,
    *,
    staged_composer_text: str,
    session_id: str | None,
) -> dict[str, Any]:
    """Build the ACP v1 frame JSON contract (pre-serialization)."""

    effective_session_id = session_id if session_id is not None else state.session_id
    turn: dict[str, Any] | None
    if state.turn_status is None:
        turn = None
    else:
        turn = {"status": state.turn_status}

    composer_text = staged_composer_text
    return {
        "v": 1,
        "session_id": effective_session_id,
        "turn": turn,
        "composer": {
            "text": composer_text,
            "staged": bool(composer_text),
        },
        "items": copy.deepcopy(state.items),
        "pending_requests": copy.deepcopy(state.pending_requests),
        "model": {
            "id": state.model_id,
            "effort": state.effort,
        },
        "usage": copy.deepcopy(state.usage),
        "stop_reason": state.stop_reason,
    }


def _apply_session_update(state: AcpViewState, update: dict[str, Any]) -> None:
    kind = update.get("sessionUpdate")
    if not isinstance(kind, str):
        return

    if kind == "agent_message_chunk":
        text = _content_text(update.get("content"))
        if text is None:
            return
        if state.turn_status is None:
            state.turn_status = "streaming"
        item_id = state._stream_message_id
        if item_id is None:
            item_id = _next_stream_id(state, "agent")
            state._stream_message_id = item_id
            state.items.append(
                {
                    "id": item_id,
                    "type": "agentMessage",
                    "role": "assistant",
                    "text": text,
                }
            )
        else:
            entry = _ensure_item(state, item_id, item_type="agentMessage", role="assistant")
            entry["text"] = str(entry.get("text") or "") + text
        return

    if kind == "user_message_chunk":
        text = _content_text(update.get("content"))
        if text is None:
            return
        item_id = state._stream_user_id
        if item_id is None:
            item_id = _next_stream_id(state, "user")
            state._stream_user_id = item_id
            state.items.append(
                {
                    "id": item_id,
                    "type": "userMessage",
                    "role": "user",
                    "text": text,
                }
            )
        else:
            entry = _ensure_item(state, item_id, item_type="userMessage", role="user")
            entry["text"] = str(entry.get("text") or "") + text
        return

    if kind == "agent_thought_chunk":
        text = _content_text(update.get("content"))
        if text is None:
            return
        if state.turn_status is None:
            state.turn_status = "streaming"
        item_id = state._stream_thought_id
        if item_id is None:
            item_id = _next_stream_id(state, "thought")
            state._stream_thought_id = item_id
            state.items.append(
                {
                    "id": item_id,
                    "type": "agentThought",
                    "role": "assistant",
                    "text": text,
                }
            )
        else:
            entry = _ensure_item(state, item_id, item_type="agentThought", role="assistant")
            entry["text"] = str(entry.get("text") or "") + text
        return

    if kind == "tool_call":
        item = _normalize_tool_call(update)
        _upsert_item(state, item)
        if state.turn_status is None:
            state.turn_status = "streaming"
        # New tool call ends current message stream segments.
        state._stream_message_id = None
        state._stream_thought_id = None
        return

    if kind == "tool_call_update":
        item = _normalize_tool_call(update)
        _upsert_item(state, item, merge=True)
        return

    if kind == "usage_update":
        usage = {key: value for key, value in update.items() if key != "sessionUpdate"}
        if usage:
            state.usage = copy.deepcopy(usage)
        return

    if kind == "current_mode_update":
        mode = update.get("currentModeId") or update.get("modeId") or update.get("mode")
        if isinstance(mode, str):
            state.current_mode = mode
        return

    if kind == "config_option_update":
        # Best-effort: pull model id from common shapes.
        options = update.get("configOptions") or update.get("options")
        if isinstance(options, list):
            for option in options:
                if not isinstance(option, dict):
                    continue
                option_id = option.get("id") or option.get("name")
                if option_id in {"model", "modelId"}:
                    value = option.get("value") or option.get("currentValue")
                    if isinstance(value, str):
                        state.model_id = value
                if option_id in {"mode", "modeId"} and isinstance(
                    option.get("value") or option.get("currentValue"), str
                ):
                    state.current_mode = option.get("value") or option.get("currentValue")
        return

    if kind == "state_update":
        # v2-forward: session state + optional stopReason after cancel/prompt end.
        session_state = update.get("sessionState") or update.get("state")
        stop_reason = update.get("stopReason")
        if isinstance(stop_reason, str):
            apply_stop_reason(state, stop_reason)
        elif session_state == "idle":
            state.turn_status = "idle"
        elif session_state == "running":
            state.turn_status = "streaming"
        elif session_state == "requires_action":
            state.turn_status = "streaming"
        return

    # Unknown sessionUpdate kinds: ignore safely.


def _content_text(content: Any) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
    return None


def _normalize_tool_call(update: dict[str, Any]) -> dict[str, Any]:
    tool_call_id = update.get("toolCallId") or update.get("id")
    item: dict[str, Any] = {
        "id": tool_call_id if isinstance(tool_call_id, str) else None,
        "type": "toolCall",
        "role": "tool",
    }
    for key in (
        "title",
        "kind",
        "status",
        "content",
        "locations",
        "rawInput",
        "rawOutput",
        "toolCallId",
    ):
        if key in update:
            item[key] = copy.deepcopy(update[key])
    if "toolCallId" not in item and isinstance(tool_call_id, str):
        item["toolCallId"] = tool_call_id
    # Flatten a short text preview when content has text blocks.
    content = item.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            text = _content_text(part)
            if text is not None:
                parts.append(text)
        if parts:
            item["text"] = "".join(parts)
    return item


def _upsert_item(
    state: AcpViewState,
    item: dict[str, Any],
    *,
    merge: bool = False,
) -> None:
    item_id = item.get("id") or item.get("toolCallId")
    if not isinstance(item_id, str) or not item_id:
        state.items.append(item)
        return
    item = {**item, "id": item_id}
    for index, existing in enumerate(state.items):
        existing_id = existing.get("id") or existing.get("toolCallId")
        if existing_id == item_id:
            if merge:
                merged = copy.deepcopy(existing)
                for key, value in item.items():
                    if value is not None:
                        merged[key] = value
                state.items[index] = merged
            else:
                state.items[index] = item
            return
    state.items.append(item)


def _ensure_item(
    state: AcpViewState,
    item_id: str,
    *,
    item_type: str,
    role: str,
) -> dict[str, Any]:
    for existing in state.items:
        if existing.get("id") == item_id:
            return existing
    entry: dict[str, Any] = {
        "id": item_id,
        "type": item_type,
        "role": role,
        "text": "",
    }
    state.items.append(entry)
    return entry


def _next_stream_id(state: AcpViewState, prefix: str) -> str:
    return f"{prefix}-{len(state.items) + 1}"


__all__ = [
    "AcpViewState",
    "TurnStatus",
    "apply_notification",
    "apply_server_request",
    "apply_stop_reason",
    "mark_prompt_started",
    "remove_pending_request",
    "to_snapshot_dict",
]
