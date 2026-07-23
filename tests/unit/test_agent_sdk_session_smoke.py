"""Smoke tests: VerifiedHarnessControlSession over a fake Agent SDK connection."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest

from murder.llm.harness_control.agent_sdk.connection import AgentSdkConnection
from murder.llm.harness_control.model.actions import InputChunk, InputProvenance
from murder.llm.harness_control.model.operations import OperationOutcome
from murder.llm.harness_control.runtime.agent_sdk_frame_observer import AgentSdkFrameObserver
from murder.llm.harness_control.runtime.prompt_driver import PromptDriverPolicy
from murder.llm.harness_control.runtime.session import VerifiedHarnessControlSession
from murder.state.persistence.schema import init_db


class FakeAgentSdkClient:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.interrupted = False
        self._outbound: asyncio.Queue[Any | None] = asyncio.Queue()
        self.connected = False

    async def connect(self, prompt: str | AsyncIterator[dict[str, Any]] | None = None) -> None:
        del prompt
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False
        await self._outbound.put(None)

    async def query(self, prompt: str, session_id: str = "default") -> None:
        del session_id
        self.queries.append(prompt)
        await self._outbound.put({"kind": "user", "text": prompt, "uuid": f"u-{len(self.queries)}"})
        await self._outbound.put(
            {
                "kind": "assistant",
                "text": f"echo:{prompt}",
                "uuid": f"a-{len(self.queries)}",
                "model": "claude-sonnet-4",
                "tool_uses": [],
            }
        )
        await self._outbound.put(
            {
                "kind": "result",
                "subtype": "success",
                "session_id": "sess-smoke",
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "result": f"echo:{prompt}",
            }
        )

    async def interrupt(self) -> None:
        self.interrupted = True
        await self._outbound.put(
            {
                "kind": "result",
                "subtype": "error_during_execution",
                "session_id": "sess-smoke",
                "usage": None,
                "result": None,
            }
        )

    async def set_model(self, model: str | None = None) -> None:
        del model

    async def receive_messages(self) -> AsyncIterator[Any]:
        while True:
            item = await self._outbound.get()
            if item is None:
                return
            yield item


async def _no_sleep(_: float) -> None:
    return None


def _db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    init_db(connection)
    return connection


async def _session(
    client: FakeAgentSdkClient,
) -> tuple[VerifiedHarnessControlSession, AgentSdkConnection]:
    connection = AgentSdkConnection(cwd="/tmp", client=client)
    await connection.start()
    session = VerifiedHarnessControlSession.from_agent_sdk(
        agent_sdk=connection,
        harness_kind="claude_code",
        terminal_session="test",
        connection=_db(),
        persistence_session_id="agent-sdk-smoke",
        prompt_policy=PromptDriverPolicy(
            observation_interval=timedelta(),
            maximum_observations=24,
        ),
        prompt_sleep=_no_sleep,
    )
    assert isinstance(session._observer, AgentSdkFrameObserver)
    return session, connection


@pytest.mark.asyncio
async def test_submit_prompt_emits_query_over_fake_agent_sdk() -> None:
    client = FakeAgentSdkClient()
    session, connection = await _session(client)
    try:
        result = await session.submit_prompt(
            (InputChunk("ping", InputProvenance.USER_PASTE_BLOCK, "chunk-1"),),
            submission_deadline=timedelta(seconds=5),
        )
        assert result.outcome is OperationOutcome.SUBMITTED
        assert client.queries == ["ping"]
        assert connection.staged_composer_text == ""
    finally:
        await connection.aclose()


@pytest.mark.asyncio
async def test_interrupt_emits_over_fake_agent_sdk() -> None:
    client = FakeAgentSdkClient()
    session, connection = await _session(client)
    try:
        connection.prompt_in_flight = True
        ok = await session.interrupt(deadline=timedelta(seconds=5))
        assert ok is True
        assert client.interrupted is True
    finally:
        await connection.aclose()
