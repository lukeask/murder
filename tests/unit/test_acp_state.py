"""Unit tests for ACP view-state notification application."""

from __future__ import annotations

import json

from murder.llm.harness_control.acp.protocol import RpcNotification, RpcRequest
from murder.llm.harness_control.acp.state import (
    AcpViewState,
    apply_notification,
    apply_server_request,
    apply_stop_reason,
    mark_prompt_started,
    remove_pending_request,
    to_snapshot_dict,
)


def _update(session_id: str, update: dict) -> RpcNotification:
    return RpcNotification(
        method="session/update",
        params={"sessionId": session_id, "update": update},
    )


def test_message_chunks_tool_call_and_snapshot() -> None:
    state = AcpViewState()
    mark_prompt_started(state)
    assert state.turn_status == "streaming"

    apply_notification(
        state,
        _update(
            "sess-1",
            {
                "sessionUpdate": "user_message_chunk",
                "content": {"type": "text", "text": "Hi"},
            },
        ),
    )
    apply_notification(
        state,
        _update(
            "sess-1",
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "Hello"},
            },
        ),
    )
    apply_notification(
        state,
        _update(
            "sess-1",
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": " world"},
            },
        ),
    )
    apply_notification(
        state,
        _update(
            "sess-1",
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "tc-1",
                "title": "Read file",
                "kind": "read",
                "status": "pending",
            },
        ),
    )
    apply_notification(
        state,
        _update(
            "sess-1",
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "tc-1",
                "status": "completed",
                "content": [{"type": "text", "text": "file contents"}],
            },
        ),
    )
    apply_notification(
        state,
        _update(
            "sess-1",
            {
                "sessionUpdate": "usage_update",
                "usedTokens": 10,
                "maxTokens": 100,
            },
        ),
    )
    apply_notification(
        state,
        RpcNotification(method="totally/unknown", params={"x": 1}),
    )
    apply_stop_reason(state, "end_turn")

    snapshot = to_snapshot_dict(state, staged_composer_text="", session_id=None)
    assert snapshot["v"] == 1
    assert snapshot["session_id"] == "sess-1"
    assert snapshot["turn"] == {"status": "completed"}
    assert snapshot["composer"] == {"text": "", "staged": False}
    assert snapshot["stop_reason"] == "end_turn"
    assert snapshot["usage"]["usedTokens"] == 10  # noqa: PLR2004

    types = [item["type"] for item in snapshot["items"]]
    assert types == ["userMessage", "agentMessage", "toolCall"]
    assert snapshot["items"][0]["text"] == "Hi"
    assert snapshot["items"][0]["role"] == "user"
    assert snapshot["items"][1]["text"] == "Hello world"
    assert snapshot["items"][1]["role"] == "assistant"
    assert snapshot["items"][2]["id"] == "tc-1"
    assert snapshot["items"][2]["status"] == "completed"
    assert snapshot["items"][2]["text"] == "file contents"
    assert snapshot["pending_requests"] == []
    assert snapshot["model"] == {"id": None, "effort": None}

    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    again = json.dumps(
        to_snapshot_dict(state, staged_composer_text="", session_id=None),
        sort_keys=True,
        separators=(",", ":"),
    )
    assert encoded == again


def test_thought_chunks_permission_pending_and_cancel() -> None:
    state = AcpViewState()
    mark_prompt_started(state)
    apply_notification(
        state,
        _update(
            "sess",
            {
                "sessionUpdate": "agent_thought_chunk",
                "content": {"type": "text", "text": "think"},
            },
        ),
    )
    apply_notification(
        state,
        _update(
            "sess",
            {
                "sessionUpdate": "agent_thought_chunk",
                "content": {"type": "text", "text": "ing"},
            },
        ),
    )
    apply_server_request(
        state,
        RpcRequest(
            id=42,
            method="session/request_permission",
            params={"toolCall": {"toolCallId": "tc"}},
        ),
    )
    apply_server_request(
        state,
        RpcRequest(
            id=43,
            method="cursor/ask_question",
            params={"toolCallId": "q1", "questions": []},
        ),
    )
    snapshot = to_snapshot_dict(state, staged_composer_text="hi", session_id="sess")
    assert snapshot["composer"] == {"text": "hi", "staged": True}
    assert snapshot["items"][0]["type"] == "agentThought"
    assert snapshot["items"][0]["text"] == "thinking"
    assert snapshot["pending_requests"] == [
        {
            "id": 42,
            "method": "session/request_permission",
            "params": {"toolCall": {"toolCallId": "tc"}},
        },
        {
            "id": 43,
            "method": "cursor/ask_question",
            "params": {"toolCallId": "q1", "questions": []},
        },
    ]
    remove_pending_request(state, 42)
    assert (
        len(
            to_snapshot_dict(state, staged_composer_text="hi", session_id="sess")[
                "pending_requests"
            ]
        )
        == 1
    )

    apply_stop_reason(state, "cancelled")
    assert state.turn_status == "cancelled"
    assert (
        to_snapshot_dict(state, staged_composer_text="", session_id="sess")["stop_reason"]
        == "cancelled"
    )


def test_mode_and_config_option_updates() -> None:
    state = AcpViewState()
    apply_notification(
        state,
        _update(
            "s",
            {"sessionUpdate": "current_mode_update", "currentModeId": "agent"},
        ),
    )
    assert state.current_mode == "agent"
    apply_notification(
        state,
        _update(
            "s",
            {
                "sessionUpdate": "config_option_update",
                "configOptions": [
                    {"id": "model", "value": "gpt-5"},
                    {"id": "mode", "currentValue": "plan"},
                ],
            },
        ),
    )
    assert state.model_id == "gpt-5"
    assert state.current_mode == "plan"


def test_idle_snapshot_when_no_turn() -> None:
    state = AcpViewState(session_id="s")
    snapshot = to_snapshot_dict(state, staged_composer_text="", session_id=None)
    assert snapshot["turn"] is None
    assert snapshot["stop_reason"] is None
    assert snapshot["session_id"] == "s"
