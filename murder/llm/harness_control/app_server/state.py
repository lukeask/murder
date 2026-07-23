# ruff: noqa: PLR0911, PLR0912
"""Mutable app-server view state and notification application.

``AppServerFrameObserver`` drains connection queues into this state, then
serializes a deterministic v1 snapshot dict into ``TerminalFrame.raw_text``.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Literal

from murder.llm.harness_control.app_server.protocol import RpcNotification, RpcRequest

TurnStatus = Literal["idle", "streaming", "completed", "interrupted", "failed"]

_TERMINAL_TURN_STATUSES = frozenset({"completed", "interrupted", "failed"})


@dataclass
class AppServerViewState:
    """Accumulated app-server thread/turn/item state for frame snapshots."""

    thread_id: str | None = None
    turn_id: str | None = None
    turn_status: TurnStatus | None = None
    items: list[dict[str, Any]] = field(default_factory=list)
    pending_requests: list[dict[str, Any]] = field(default_factory=list)
    model_id: str | None = None
    effort: str | None = None
    usage: dict[str, Any] | None = None


def apply_notification(state: AppServerViewState, notification: RpcNotification) -> None:
    """Apply one server notification to ``state``. Unknown methods are ignored."""

    method = notification.method
    params = notification.params if isinstance(notification.params, dict) else {}

    if method == "thread/started":
        thread = params.get("thread")
        if isinstance(thread, dict):
            thread_id = thread.get("id")
            if isinstance(thread_id, str) and thread_id:
                state.thread_id = thread_id
        return

    if method == "turn/started":
        thread_id = params.get("threadId")
        if isinstance(thread_id, str) and thread_id:
            state.thread_id = thread_id
        turn = params.get("turn")
        if isinstance(turn, dict):
            turn_id = turn.get("id")
            if isinstance(turn_id, str) and turn_id:
                state.turn_id = turn_id
            state.turn_status = "streaming"
            # New turn: reset item accumulation for this turn.
            state.items = []
            turn_items = turn.get("items")
            if isinstance(turn_items, list):
                for raw in turn_items:
                    if isinstance(raw, dict):
                        state.items.append(_normalize_item(raw))
        return

    if method == "turn/completed":
        thread_id = params.get("threadId")
        if isinstance(thread_id, str) and thread_id:
            state.thread_id = thread_id
        turn = params.get("turn")
        if isinstance(turn, dict):
            turn_id = turn.get("id")
            if isinstance(turn_id, str) and turn_id:
                state.turn_id = turn_id
            state.turn_status = _map_turn_status(turn.get("status"))
            turn_items = turn.get("items")
            if isinstance(turn_items, list) and turn_items:
                # Prefer authoritative completed turn items when provided.
                state.items = [
                    _normalize_item(raw) for raw in turn_items if isinstance(raw, dict)
                ]
        return

    if method == "item/started":
        item = params.get("item")
        if isinstance(item, dict):
            _upsert_item(state, _normalize_item(item))
        return

    if method == "item/completed":
        item = params.get("item")
        if isinstance(item, dict):
            _upsert_item(state, _normalize_item(item), replace=True)
        return

    if method == "item/agentMessage/delta":
        item_id = params.get("itemId")
        delta = params.get("delta")
        if isinstance(item_id, str) and isinstance(delta, str):
            entry = _ensure_item(state, item_id, item_type="agentMessage")
            entry["text"] = str(entry.get("text") or "") + delta
        return

    if method in {
        "item/reasoning/textDelta",
        "item/reasoning/summaryTextDelta",
        "item/reasoning/summaryPartAdded",
    }:
        item_id = params.get("itemId")
        if not isinstance(item_id, str):
            return
        entry = _ensure_item(state, item_id, item_type="reasoning")
        if method == "item/reasoning/textDelta":
            delta = params.get("delta")
            if isinstance(delta, str):
                content = entry.setdefault("content", [])
                if not isinstance(content, list):
                    content = []
                    entry["content"] = content
                index = params.get("contentIndex")
                _append_indexed_delta(content, index, delta)
                entry["text"] = "".join(str(part) for part in content if isinstance(part, str))
        elif method == "item/reasoning/summaryTextDelta":
            delta = params.get("delta")
            if isinstance(delta, str):
                summary = entry.setdefault("summary", [])
                if not isinstance(summary, list):
                    summary = []
                    entry["summary"] = summary
                index = params.get("summaryIndex")
                _append_indexed_delta(summary, index, delta)
                entry["text"] = "\n".join(str(part) for part in summary if isinstance(part, str))
        elif method == "item/reasoning/summaryPartAdded":
            summary = entry.setdefault("summary", [])
            if not isinstance(summary, list):
                summary = []
                entry["summary"] = summary
            summary.append("")
        return

    if method == "item/commandExecution/outputDelta":
        item_id = params.get("itemId")
        delta = params.get("delta")
        if isinstance(item_id, str) and isinstance(delta, str):
            entry = _ensure_item(state, item_id, item_type="commandExecution")
            prior = entry.get("aggregatedOutput")
            if not isinstance(prior, str):
                prior = str(entry.get("text") or "")
            entry["aggregatedOutput"] = prior + delta
            entry["text"] = entry["aggregatedOutput"]
        return

    if method == "thread/tokenUsage/updated":
        usage = params.get("tokenUsage")
        if isinstance(usage, dict):
            state.usage = copy.deepcopy(usage)
        return

    # Unknown methods: ignore safely.


def apply_server_request(state: AppServerViewState, request: RpcRequest) -> None:
    """Record a server→client request (approvals / elicitation) as pending."""

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


def remove_pending_request(state: AppServerViewState, request_id: str | int) -> None:
    """Drop a pending request by JSON-RPC id after it has been answered."""

    state.pending_requests = [
        entry for entry in state.pending_requests if entry.get("id") != request_id
    ]


def to_snapshot_dict(
    state: AppServerViewState,
    *,
    staged_composer_text: str,
    thread_id: str | None,
) -> dict[str, Any]:
    """Build the v1 frame JSON contract (pre-serialization)."""

    effective_thread_id = thread_id if thread_id is not None else state.thread_id
    turn: dict[str, Any] | None
    if state.turn_id is None and state.turn_status is None:
        turn = None
    else:
        status: TurnStatus = state.turn_status or "idle"
        turn = {
            "id": state.turn_id or "",
            "status": status,
        }

    composer_text = staged_composer_text
    return {
        "v": 1,
        "thread_id": effective_thread_id,
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
    }


def _map_turn_status(raw: Any) -> TurnStatus:
    if raw in _TERMINAL_TURN_STATUSES:
        return raw  # type: ignore[return-value]
    if raw == "inProgress":
        return "streaming"
    if raw == "idle":
        return "idle"
    return "completed" if raw is None else "failed"


def _normalize_item(raw: dict[str, Any]) -> dict[str, Any]:
    item = copy.deepcopy(raw)
    item_type = item.get("type")
    if item_type == "agentMessage" and "text" not in item:
        item["text"] = ""
    if item_type == "userMessage" and "text" not in item:
        content = item.get("content")
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    parts.append(part["text"])
            item["text"] = "".join(parts)
        else:
            item["text"] = ""
    if item_type == "reasoning":
        if "text" not in item:
            content = item.get("content")
            summary = item.get("summary")
            if isinstance(summary, list) and summary:
                item["text"] = "\n".join(str(part) for part in summary if isinstance(part, str))
            elif isinstance(content, list):
                item["text"] = "".join(str(part) for part in content if isinstance(part, str))
            else:
                item["text"] = ""
        item.setdefault("content", [])
        item.setdefault("summary", [])
    if item_type == "commandExecution":
        aggregated = item.get("aggregatedOutput")
        if isinstance(aggregated, str):
            item["text"] = aggregated
        else:
            item.setdefault("text", "")
    return item


def _upsert_item(
    state: AppServerViewState,
    item: dict[str, Any],
    *,
    replace: bool = False,
) -> None:
    item_id = item.get("id")
    if not isinstance(item_id, str) or not item_id:
        state.items.append(item)
        return
    for index, existing in enumerate(state.items):
        if existing.get("id") == item_id:
            if replace:
                state.items[index] = item
            else:
                merged = copy.deepcopy(existing)
                merged.update(item)
                # Preserve accumulated text if the new payload has an empty placeholder.
                if not merged.get("text") and existing.get("text"):
                    merged["text"] = existing["text"]
                state.items[index] = merged
            return
    state.items.append(item)


def _ensure_item(
    state: AppServerViewState,
    item_id: str,
    *,
    item_type: str,
) -> dict[str, Any]:
    for existing in state.items:
        if existing.get("id") == item_id:
            return existing
    entry: dict[str, Any] = {"id": item_id, "type": item_type, "text": ""}
    if item_type == "reasoning":
        entry["content"] = []
        entry["summary"] = []
    state.items.append(entry)
    return entry


def _append_indexed_delta(parts: list[Any], index: Any, delta: str) -> None:
    if isinstance(index, int) and not isinstance(index, bool):
        while len(parts) <= index:
            parts.append("")
        current = parts[index]
        parts[index] = (current if isinstance(current, str) else "") + delta
    elif parts and isinstance(parts[-1], str):
        parts[-1] = parts[-1] + delta
    else:
        parts.append(delta)


__all__ = [
    "AppServerViewState",
    "TurnStatus",
    "apply_notification",
    "apply_server_request",
    "remove_pending_request",
    "to_snapshot_dict",
]
