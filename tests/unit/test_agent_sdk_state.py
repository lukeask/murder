"""Unit tests for Claude Agent SDK view-state event application."""

from __future__ import annotations

import json

from murder.llm.harness_control.agent_sdk.state import (
    AgentSdkViewState,
    apply_event,
    apply_permission_request,
    remove_pending_request,
    to_snapshot_dict,
)


def test_turn_lifecycle_builds_stable_snapshot() -> None:
    state = AgentSdkViewState()
    apply_event(
        state,
        {"kind": "user", "text": "hello", "uuid": "u1"},
    )
    apply_event(
        state,
        {
            "kind": "assistant",
            "text": "Hello",
            "uuid": "a1",
            "model": "claude-sonnet-4",
            "tool_uses": [],
        },
    )
    apply_event(
        state,
        {
            "kind": "assistant",
            "text": "Hello world",
            "uuid": "a1",
            "model": "claude-sonnet-4",
            "tool_uses": [],
        },
    )
    apply_event(
        state,
        {
            "kind": "result",
            "subtype": "success",
            "session_id": "sess-1",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "result": "Hello world",
        },
    )
    snapshot = to_snapshot_dict(state, staged_composer_text="", session_id=None)
    assert snapshot["session_id"] == "sess-1"
    assert snapshot["turn"] == {"status": "completed"}
    assert snapshot["model"]["id"] == "claude-sonnet-4"
    assert any(item.get("text") == "Hello world" for item in snapshot["items"])
    # Deterministic JSON for idle frames.
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    assert json.loads(encoded) == snapshot


def test_permission_and_question_pending_requests() -> None:
    state = AgentSdkViewState()
    apply_permission_request(
        state,
        {
            "id": "req-1",
            "tool_name": "Bash",
            "input": {"command": "ls"},
            "title": "Run ls",
        },
    )
    apply_permission_request(
        state,
        {
            "id": "req-2",
            "tool_name": "AskUserQuestion",
            "input": {
                "questions": [
                    {
                        "question": "Format?",
                        "options": [{"label": "Summary"}],
                        "multiSelect": False,
                    }
                ]
            },
        },
    )
    assert state.pending_requests[0]["method"] == "tool/can_use_tool"
    assert state.pending_requests[1]["method"] == "tool/AskUserQuestion"
    remove_pending_request(state, "req-1")
    assert len(state.pending_requests) == 1
    assert state.pending_requests[0]["id"] == "req-2"


def test_tool_use_and_result() -> None:
    state = AgentSdkViewState()
    apply_event(
        state,
        {
            "kind": "assistant",
            "text": "",
            "uuid": "a1",
            "tool_uses": [{"id": "t1", "name": "Bash", "input": {"command": "pwd"}}],
        },
    )
    apply_event(
        state,
        {
            "kind": "tool_result",
            "tool_use_id": "t1",
            "content": "/tmp",
            "is_error": False,
        },
    )
    tool = next(item for item in state.items if item.get("id") == "t1")
    assert tool["running"] is False
    assert tool["result"] == "/tmp"
