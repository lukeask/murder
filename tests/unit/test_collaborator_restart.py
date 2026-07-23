"""Collaborator tmux death -> graceful restart (Objective 6).

A TmuxError from agent.send when the session is genuinely dead must trigger one
ensure_collaborator respawn + send retry and surface a one-line notice; a
transient TmuxError while the session is still alive must propagate unchanged.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from murder.runtime.terminal.tmux import TmuxError
from murder.runtime.workers.base import WorkerCtx
from murder.runtime.workers.collaborator_worker import CollaboratorWorker
from murder.runtime.orchestration.commands import OrchestrationCommand


def _agent(*, send=None, live=True):
    agent = MagicMock()
    agent.send = send or AsyncMock(return_value=None)
    agent.is_live = AsyncMock(return_value=live)
    agent.record_user_block_event = AsyncMock()
    agent.record_notice_block_event = AsyncMock()
    return agent


def _ctx():
    return MagicMock(spec=WorkerCtx)


def test_dead_session_respawns_retries_once_and_surfaces_notice():
    dead_agent = _agent(send=AsyncMock(side_effect=TmuxError("session gone")), live=False)
    fresh_agent = _agent()
    agents = {"collaborator-0": dead_agent, "collaborator-1": fresh_agent}
    ensure = AsyncMock(side_effect=["collaborator-0", "collaborator-1"])

    worker = CollaboratorWorker(
        ensure_collaborator=ensure,
        get_agent=lambda agent_id: agents.get(agent_id),
    )

    result = asyncio.run(
        worker._dispatch(OrchestrationCommand.COLLABORATOR_CHAT_SEND, {"text": "hello"}, _ctx())
    )

    assert result == {"handled": True, "agent_id": "collaborator-1"}
    assert ensure.await_count == 2
    fresh_agent.send.assert_awaited_once_with("hello")
    fresh_agent.record_notice_block_event.assert_awaited_once()
    notice_args = fresh_agent.record_notice_block_event.await_args
    assert "restarted" in notice_args.args[0]
    assert notice_args.kwargs.get("severity") == "warning"
    # The user's text is still recorded as ground truth on the new agent.
    fresh_agent.record_user_block_event.assert_awaited_once_with("hello")


def test_transient_tmux_error_with_live_session_propagates():
    agent = _agent(send=AsyncMock(side_effect=TmuxError("flake")), live=True)
    ensure = AsyncMock(return_value="collaborator-0")

    worker = CollaboratorWorker(
        ensure_collaborator=ensure,
        get_agent=lambda agent_id: agent,
    )

    with pytest.raises(TmuxError):
        asyncio.run(
            worker._dispatch(OrchestrationCommand.COLLABORATOR_CHAT_SEND, {"text": "hello"}, _ctx())
        )
    assert ensure.await_count == 1  # no respawn attempt
    agent.record_notice_block_event.assert_not_awaited()


def test_happy_path_unchanged():
    agent = _agent()
    ensure = AsyncMock(return_value="collaborator-0")
    worker = CollaboratorWorker(
        ensure_collaborator=ensure,
        get_agent=lambda agent_id: agent,
    )

    result = asyncio.run(
        worker._dispatch(OrchestrationCommand.COLLABORATOR_CHAT_SEND, {"text": "hi"}, _ctx())
    )

    assert result == {"handled": True, "agent_id": "collaborator-0"}
    assert ensure.await_count == 1
    agent.record_notice_block_event.assert_not_awaited()
