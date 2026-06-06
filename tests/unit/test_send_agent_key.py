"""Orchestrator raw-key delivery to harness tmux sessions."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from murder.runtime.orchestration.orchestrator import Orchestrator
from murder.state.persistence.agents import get_agent_messages
from murder.state.persistence.schema import get_db, init_db
from tests.support.fake_tmux import FakeTmux


def test_send_agent_key_targets_agent_session(fake_tmux: FakeTmux) -> None:
    agent = SimpleNamespace(session="murder_demo_crow_t001")
    rt = SimpleNamespace(
        get_agent=lambda agent_id: agent if agent_id == "crow-t001" else None,
    )
    orch = Orchestrator(rt)

    result = asyncio.run(orch.send_agent_key("crow-t001", "Up"))

    assert result["handled"] is True
    assert result["agent_id"] == "crow-t001"
    assert result["session"] == "murder_demo_crow_t001"
    assert result["key"] == "Up"
    assert result["literal"] is False
    assert result["enter"] is False
    assert result["logged_user_input"] is False
    assert fake_tmux.calls_to("send_keys") == [
        (("murder_demo_crow_t001", "Up"), {"literal": False, "enter": False}),
    ]


def test_send_agent_key_without_agent_id_ensures_collaborator(
    fake_tmux: FakeTmux,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = SimpleNamespace(session="murder_demo_collaborator")
    rt = SimpleNamespace(
        get_agent=lambda agent_id: agent if agent_id == "collaborator-1" else None,
    )
    orch = Orchestrator(rt)

    async def _ensure() -> str:
        return "collaborator-1"

    monkeypatch.setattr(orch, "ensure_collaborator", _ensure)

    result = asyncio.run(orch.send_agent_key(None, "Down"))

    assert result["handled"] is True
    assert result["enter"] is False
    assert result["logged_user_input"] is False
    send_calls = fake_tmux.calls_to("send_keys")
    assert send_calls[0][0] == ("murder_demo_collaborator", "Down")
    assert send_calls[0][1]["literal"] is False
    assert send_calls[0][1]["enter"] is False


def test_send_agent_key_literal_text(fake_tmux: FakeTmux) -> None:
    agent = SimpleNamespace(session="murder_demo_crow_t001")
    rt = SimpleNamespace(get_agent=lambda _agent_id: agent)
    orch = Orchestrator(rt)

    result = asyncio.run(orch.send_agent_key("crow-t001", "x", literal=True))

    assert result["literal"] is True
    assert result["enter"] is False
    assert result["logged_user_input"] is False
    assert fake_tmux.calls_to("send_keys")[0][1]["literal"] is True


def test_send_agent_key_can_submit_enter_and_log_user_input(
    fake_tmux: FakeTmux,
    repo_root: Path,
) -> None:
    db = get_db(repo_root / ".murder" / "murder.db")
    init_db(db)
    agent = SimpleNamespace(session="murder_demo_crow_t001")
    rt = SimpleNamespace(get_agent=lambda _agent_id: agent, db=db)
    orch = Orchestrator(rt)

    result = asyncio.run(
        orch.send_agent_key(
            "crow-t001",
            "2",
            literal=True,
            enter=True,
            log_user_input="2. No, exit",
        )
    )

    assert result["enter"] is True
    assert result["logged_user_input"] is True
    assert fake_tmux.calls_to("send_keys") == [
        (("murder_demo_crow_t001", "2"), {"literal": True, "enter": True}),
    ]
    assert get_agent_messages(db, "crow-t001") == [
        {
            "ordinal": 0,
            "role": "user",
            "body": "2. No, exit",
            "captured_at": get_agent_messages(db, "crow-t001")[0]["captured_at"],
        }
    ]
