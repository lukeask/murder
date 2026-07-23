# ruff: noqa: PLR0911, PLR0912, PLR0915
"""Mutable Claude Agent SDK view state and event application.

``AgentSdkFrameObserver`` drains connection queues into this state, then
serializes a deterministic v1 snapshot dict into ``TerminalFrame.raw_text``.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Literal

TurnStatus = Literal["idle", "streaming", "completed", "interrupted", "failed"]

_PERMISSION_METHOD = "tool/can_use_tool"
_QUESTION_METHOD = "tool/AskUserQuestion"


@dataclass
class AgentSdkViewState:
    """Accumulated Agent SDK session/turn/item state for frame snapshots."""

    session_id: str | None = None
    turn_status: TurnStatus | None = None
    items: list[dict[str, Any]] = field(default_factory=list)
    pending_requests: list[dict[str, Any]] = field(default_factory=list)
    model_id: str | None = None
    effort: str | None = None
    usage: dict[str, Any] | None = None
    last_result_subtype: str | None = None


def apply_event(state: AgentSdkViewState, event: dict[str, Any]) -> None:
    """Apply one normalized Agent SDK event to ``state``. Unknown kinds ignored."""

    kind = event.get("kind")
    if kind == "user":
        text = event.get("text")
        if isinstance(text, str) and text:
            state.items.append(
                {
                    "id": str(event.get("uuid") or f"user:{len(state.items)}"),
                    "type": "user",
                    "text": text,
                }
            )
        state.turn_status = "streaming"
        return

    if kind == "assistant":
        item_id = str(event.get("uuid") or event.get("message_id") or f"asst:{len(state.items)}")
        model = event.get("model")
        if isinstance(model, str) and model:
            state.model_id = model
        text = event.get("text")
        if isinstance(text, str) and text:
            _upsert_item(
                state,
                {
                    "id": item_id,
                    "type": "assistant",
                    "text": text,
                    "phase": "intermediate" if event.get("partial") else "final",
                },
                replace=not bool(event.get("partial")),
            )
        for tool in event.get("tool_uses") or []:
            if not isinstance(tool, dict):
                continue
            tool_id = str(tool.get("id") or f"tool:{len(state.items)}")
            name = str(tool.get("name") or "tool")
            tool_input = tool.get("input")
            title = name
            command = None
            if isinstance(tool_input, dict):
                if isinstance(tool_input.get("command"), str):
                    command = tool_input["command"]
                    title = f"{name}: {command}"
                elif isinstance(tool_input.get("file_path"), str):
                    title = f"{name}: {tool_input['file_path']}"
            _upsert_item(
                state,
                {
                    "id": tool_id,
                    "type": "tool_call",
                    "title": title,
                    "input": command
                    if command is not None
                    else (str(tool_input) if tool_input is not None else None),
                    "running": True,
                    "elided": False,
                },
            )
        state.turn_status = "streaming"
        return

    if kind == "tool_result":
        tool_use_id = event.get("tool_use_id")
        if isinstance(tool_use_id, str) and tool_use_id:
            entry = _find_item(state, tool_use_id)
            if entry is not None:
                content = event.get("content")
                entry["result"] = content if isinstance(content, str) else str(content or "")
                entry["running"] = False
                if event.get("is_error"):
                    entry["elided"] = False
        return

    if kind == "result":
        session_id = event.get("session_id")
        if isinstance(session_id, str) and session_id:
            state.session_id = session_id
        subtype = event.get("subtype")
        state.last_result_subtype = str(subtype) if subtype is not None else None
        usage = event.get("usage")
        if isinstance(usage, dict):
            state.usage = copy.deepcopy(usage)
            if event.get("total_cost_usd") is not None:
                state.usage["total_cost_usd"] = event.get("total_cost_usd")
        if subtype == "success":
            state.turn_status = "completed"
            result_text = event.get("result")
            if isinstance(result_text, str) and result_text:
                # Ensure final assistant text is present even if no AssistantMessage landed.
                if not any(
                    isinstance(item, dict) and item.get("type") == "assistant"
                    for item in state.items
                ):
                    state.items.append(
                        {
                            "id": f"result:{session_id or 'final'}",
                            "type": "assistant",
                            "text": result_text,
                            "phase": "final",
                        }
                    )
                else:
                    for item in reversed(state.items):
                        if isinstance(item, dict) and item.get("type") == "assistant":
                            item["phase"] = "final"
                            break
        elif subtype == "error_during_execution":
            state.turn_status = "interrupted"
        else:
            state.turn_status = "failed"
        return

    if kind == "system":
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        session_id = data.get("session_id") or event.get("session_id")
        if isinstance(session_id, str) and session_id:
            state.session_id = session_id
        model = data.get("model")
        if isinstance(model, str) and model:
            state.model_id = model
        return

    if kind == "stream_delta":
        item_id = str(event.get("item_id") or "stream")
        delta = event.get("delta")
        if isinstance(delta, str) and delta:
            entry = _ensure_item(state, item_id, item_type="assistant")
            entry["text"] = str(entry.get("text") or "") + delta
            entry["phase"] = "intermediate"
            state.turn_status = "streaming"
        return


def apply_permission_request(state: AgentSdkViewState, request: dict[str, Any]) -> None:
    """Record a pending ``can_use_tool`` / AskUserQuestion request."""

    tool_name = str(request.get("tool_name") or "")
    method = _QUESTION_METHOD if tool_name == "AskUserQuestion" else _PERMISSION_METHOD
    params = copy.deepcopy(request.get("input") if isinstance(request.get("input"), dict) else {})
    params["tool"] = tool_name
    if isinstance(request.get("description"), str):
        params["description"] = request["description"]
    if isinstance(request.get("title"), str):
        params.setdefault("description", request["title"])
    if isinstance(params.get("command"), str):
        pass
    elif isinstance(params.get("file_path"), str):
        params.setdefault("command", params["file_path"])
    state.pending_requests.append(
        {
            "id": request.get("id"),
            "method": method,
            "params": params,
            "tool_name": tool_name,
            "raw_input": copy.deepcopy(request.get("input")),
        }
    )


def remove_pending_request(state: AgentSdkViewState, request_id: str | int) -> None:
    """Drop a pending request by id after it has been answered."""

    state.pending_requests = [
        entry for entry in state.pending_requests if entry.get("id") != request_id
    ]


def to_snapshot_dict(
    state: AgentSdkViewState,
    *,
    staged_composer_text: str,
    session_id: str | None,
) -> dict[str, Any]:
    """Build the v1 frame JSON contract (pre-serialization)."""

    effective_session_id = session_id if session_id is not None else state.session_id
    turn: dict[str, Any] | None
    if state.turn_status is None:
        turn = None
    else:
        turn = {"status": state.turn_status or "idle"}
    return {
        "v": 1,
        "session_id": effective_session_id,
        "turn": turn,
        "composer": {
            "text": staged_composer_text,
            "staged": bool(staged_composer_text),
        },
        "items": copy.deepcopy(state.items),
        "pending_requests": copy.deepcopy(state.pending_requests),
        "model": {"id": state.model_id, "effort": state.effort},
        "usage": copy.deepcopy(state.usage),
    }


def _find_item(state: AgentSdkViewState, item_id: str) -> dict[str, Any] | None:
    for item in state.items:
        if isinstance(item, dict) and item.get("id") == item_id:
            return item
    return None


def _ensure_item(
    state: AgentSdkViewState, item_id: str, *, item_type: str
) -> dict[str, Any]:
    existing = _find_item(state, item_id)
    if existing is not None:
        return existing
    entry: dict[str, Any] = {"id": item_id, "type": item_type, "text": ""}
    state.items.append(entry)
    return entry


def _upsert_item(
    state: AgentSdkViewState, item: dict[str, Any], *, replace: bool = False
) -> None:
    item_id = item.get("id")
    if not isinstance(item_id, str):
        state.items.append(item)
        return
    existing = _find_item(state, item_id)
    if existing is None:
        state.items.append(item)
        return
    if replace:
        existing.clear()
        existing.update(item)
        return
    # Partial assistant text: append when prior was intermediate.
    if item.get("type") == "assistant" and isinstance(item.get("text"), str):
        prior = str(existing.get("text") or "")
        incoming = item["text"]
        if incoming.startswith(prior):
            existing["text"] = incoming
        elif prior and not prior.endswith(incoming):
            existing["text"] = prior + incoming
        else:
            existing["text"] = incoming
        if item.get("phase"):
            existing["phase"] = item["phase"]
        return
    existing.update(item)


__all__ = [
    "AgentSdkViewState",
    "TurnStatus",
    "apply_event",
    "apply_permission_request",
    "remove_pending_request",
    "to_snapshot_dict",
]
