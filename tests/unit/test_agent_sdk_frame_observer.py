"""Regression tests for Agent SDK observer request reconciliation."""

from __future__ import annotations

import asyncio
import json

import pytest

from murder.llm.harness_control.agent_sdk.connection import AgentSdkConnection
from murder.llm.harness_control.model.evidence import HarnessId
from murder.llm.harness_control.runtime.agent_sdk_frame_observer import AgentSdkFrameObserver


@pytest.mark.asyncio
async def test_successful_response_removes_request_from_next_observation() -> None:
    connection = AgentSdkConnection(cwd="/tmp")
    pending = asyncio.create_task(
        connection._can_use_tool("AskUserQuestion", {"questions": []}, context=None)
    )
    await asyncio.sleep(0)
    observer = AgentSdkFrameObserver(connection, HarnessId("claude_code"))

    first = json.loads((await observer.capture_frame()).raw_text)
    request_id = first["pending_requests"][0]["id"]

    await connection.respond_permission(
        request_id,
        behavior="allow",
        updated_input={"questions": [{"answer": "Yes"}]},
    )

    second = json.loads((await observer.capture_frame()).raw_text)
    assert second["pending_requests"] == []
    assert await pending == {
        "behavior": "allow",
        "updated_input": {"questions": [{"answer": "Yes"}]},
    }
