"""Unit tests for app-server view-state notification application."""

from __future__ import annotations

import json

from murder.llm.harness_control.app_server.protocol import RpcNotification, RpcRequest
from murder.llm.harness_control.app_server.state import (
    AppServerViewState,
    apply_notification,
    apply_server_request,
    remove_pending_request,
    to_snapshot_dict,
)


def test_thread_and_turn_lifecycle_builds_stable_snapshot() -> None:
    state = AppServerViewState()
    apply_notification(
        state,
        RpcNotification(
            method="thread/started",
            params={"thread": {"id": "th-1", "preview": ""}},
        ),
    )
    apply_notification(
        state,
        RpcNotification(
            method="turn/started",
            params={
                "threadId": "th-1",
                "turn": {"id": "tu-1", "status": "inProgress", "items": []},
            },
        ),
    )
    apply_notification(
        state,
        RpcNotification(
            method="item/started",
            params={
                "threadId": "th-1",
                "turnId": "tu-1",
                "item": {"id": "it-1", "type": "agentMessage", "text": ""},
                "startedAtMs": 1,
            },
        ),
    )
    apply_notification(
        state,
        RpcNotification(
            method="item/agentMessage/delta",
            params={
                "threadId": "th-1",
                "turnId": "tu-1",
                "itemId": "it-1",
                "delta": "Hello",
            },
        ),
    )
    apply_notification(
        state,
        RpcNotification(
            method="item/agentMessage/delta",
            params={
                "threadId": "th-1",
                "turnId": "tu-1",
                "itemId": "it-1",
                "delta": " world",
            },
        ),
    )
    apply_notification(
        state,
        RpcNotification(
            method="item/completed",
            params={
                "threadId": "th-1",
                "turnId": "tu-1",
                "item": {"id": "it-1", "type": "agentMessage", "text": "Hello world"},
                "completedAtMs": 2,
            },
        ),
    )
    apply_notification(
        state,
        RpcNotification(
            method="turn/completed",
            params={
                "threadId": "th-1",
                "turn": {
                    "id": "tu-1",
                    "status": "completed",
                    "items": [{"id": "it-1", "type": "agentMessage", "text": "Hello world"}],
                },
            },
        ),
    )
    apply_notification(
        state,
        RpcNotification(method="totally/unknown", params={"x": 1}),
    )

    snapshot = to_snapshot_dict(state, staged_composer_text="", thread_id=None)
    assert snapshot["v"] == 1
    assert snapshot["thread_id"] == "th-1"
    assert snapshot["turn"] == {"id": "tu-1", "status": "completed"}
    assert snapshot["composer"] == {"text": "", "staged": False}
    assert snapshot["items"][0]["text"] == "Hello world"
    assert snapshot["pending_requests"] == []
    assert snapshot["model"] == {"id": None, "effort": None}
    assert snapshot["usage"] is None

    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    again = json.dumps(
        to_snapshot_dict(state, staged_composer_text="", thread_id=None),
        sort_keys=True,
        separators=(",", ":"),
    )
    assert encoded == again


def test_reasoning_deltas_and_pending_requests() -> None:
    state = AppServerViewState()
    apply_notification(
        state,
        RpcNotification(
            method="turn/started",
            params={"threadId": "th", "turn": {"id": "tu", "status": "inProgress", "items": []}},
        ),
    )
    apply_notification(
        state,
        RpcNotification(
            method="item/started",
            params={
                "threadId": "th",
                "turnId": "tu",
                "item": {"id": "r1", "type": "reasoning"},
                "startedAtMs": 1,
            },
        ),
    )
    apply_notification(
        state,
        RpcNotification(
            method="item/reasoning/summaryTextDelta",
            params={
                "threadId": "th",
                "turnId": "tu",
                "itemId": "r1",
                "summaryIndex": 0,
                "delta": "think",
            },
        ),
    )
    apply_server_request(
        state,
        RpcRequest(
            id=42,
            method="item/commandExecution/requestApproval",
            params={"command": "ls"},
        ),
    )
    snapshot = to_snapshot_dict(state, staged_composer_text="hi", thread_id="th")
    assert snapshot["composer"] == {"text": "hi", "staged": True}
    assert snapshot["items"][0]["text"] == "think"
    assert snapshot["pending_requests"] == [
        {"id": 42, "method": "item/commandExecution/requestApproval", "params": {"command": "ls"}}
    ]
    remove_pending_request(state, 42)
    assert to_snapshot_dict(state, staged_composer_text="hi", thread_id="th")[
        "pending_requests"
    ] == []


def test_token_usage_notification() -> None:
    state = AppServerViewState()
    apply_notification(
        state,
        RpcNotification(
            method="thread/tokenUsage/updated",
            params={
                "threadId": "th",
                "turnId": "tu",
                "tokenUsage": {
                    "last": {
                        "inputTokens": 1,
                        "outputTokens": 2,
                        "cachedInputTokens": 0,
                        "reasoningOutputTokens": 0,
                        "totalTokens": 3,
                    },
                    "total": {
                        "inputTokens": 1,
                        "outputTokens": 2,
                        "cachedInputTokens": 0,
                        "reasoningOutputTokens": 0,
                        "totalTokens": 3,
                    },
                },
            },
        ),
    )
    assert state.usage is not None
    assert state.usage["total"]["totalTokens"] == 3


def test_error_notification_and_failed_turn_preserve_usage_limit() -> None:
    state = AppServerViewState()
    apply_notification(
        state,
        RpcNotification(
            method="turn/started",
            params={
                "threadId": "th-limit",
                "turn": {"id": "tu-limit", "status": "inProgress", "items": []},
            },
        ),
    )
    apply_notification(
        state,
        RpcNotification(
            method="item/started",
            params={
                "threadId": "th-limit",
                "turnId": "tu-limit",
                "item": {
                    "id": "u1",
                    "type": "userMessage",
                    "text": "hello",
                },
            },
        ),
    )
    limit_message = (
        "You've hit your usage limit. Upgrade to Pro or try again at 5:54 PM."
    )
    apply_notification(
        state,
        RpcNotification(
            method="error",
            params={
                "threadId": "th-limit",
                "turnId": "tu-limit",
                "willRetry": False,
                "error": {
                    "message": limit_message,
                    "codexErrorInfo": "usageLimitExceeded",
                },
            },
        ),
    )
    assert state.last_error is not None
    assert state.last_error["codexErrorInfo"] == "usageLimitExceeded"
    assert state.last_error["message"] == limit_message

    apply_notification(
        state,
        RpcNotification(
            method="turn/completed",
            params={
                "threadId": "th-limit",
                "turn": {
                    "id": "tu-limit",
                    "status": "failed",
                    "items": [
                        {"id": "u1", "type": "userMessage", "text": "hello"},
                    ],
                    "error": {
                        "message": limit_message,
                        "codexErrorInfo": "usageLimitExceeded",
                    },
                },
            },
        ),
    )
    assert state.turn_status == "failed"
    assert state.last_error is not None
    assert state.last_error["message"] == limit_message

    snapshot = to_snapshot_dict(state, staged_composer_text="", thread_id=None)
    assert snapshot["turn"] == {
        "id": "tu-limit",
        "status": "failed",
        "error": {
            "message": limit_message,
            "codexErrorInfo": "usageLimitExceeded",
        },
    }
    assert snapshot["items"][0]["text"] == "hello"

    # A subsequent successful turn clears the terminal error.
    apply_notification(
        state,
        RpcNotification(
            method="turn/started",
            params={
                "threadId": "th-limit",
                "turn": {"id": "tu-next", "status": "inProgress", "items": []},
            },
        ),
    )
    assert state.last_error is None
    apply_notification(
        state,
        RpcNotification(
            method="turn/completed",
            params={
                "threadId": "th-limit",
                "turn": {"id": "tu-next", "status": "completed", "items": []},
            },
        ),
    )
    cleared = to_snapshot_dict(state, staged_composer_text="", thread_id=None)
    assert cleared["turn"] == {"id": "tu-next", "status": "completed"}
    assert "error" not in cleared["turn"]


def test_rate_limits_updated_merges_into_usage() -> None:
    state = AppServerViewState()
    apply_notification(
        state,
        RpcNotification(
            method="account/rateLimits/updated",
            params={
                "rateLimits": {
                    "primary": {"usedPercent": 100, "windowDurationMins": 300},
                    "secondary": None,
                    "rateLimitReachedType": "primary",
                }
            },
        ),
    )
    assert state.usage is not None
    assert state.usage["rateLimits"]["primary"]["usedPercent"] == 100

    apply_notification(
        state,
        RpcNotification(
            method="thread/tokenUsage/updated",
            params={"tokenUsage": {"total": {"totalTokens": 9}}},
        ),
    )
    assert state.usage["total"]["totalTokens"] == 9
    assert state.usage["rateLimits"]["primary"]["usedPercent"] == 100
