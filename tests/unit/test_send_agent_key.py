"""Orchestrator raw-key delivery to harness tmux sessions."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from murder.orchestration.orchestrator import Orchestrator
from tests.support.fake_tmux import FakeTmux


def test_send_agent_key_targets_agent_session(fake_tmux: FakeTmux) -> None:
    agent = SimpleNamespace(session="murder_demo_crow_t001")
    rt = SimpleNamespace(
        get_agent=lambda agent_id: agent if agent_id == "crow-t001" else None,
    )
    orch = Orchestrator(rt)

    result = asyncio.run(orch.send_agent_key("crow-t001", "Up"))

    assert result == {
        "handled": True,
        "agent_id": "crow-t001",
        "session": "murder_demo_crow_t001",
        "key": "Up",
        "literal": False,
    }
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
    send_calls = fake_tmux.calls_to("send_keys")
    assert send_calls[0][0] == ("murder_demo_collaborator", "Down")
    assert send_calls[0][1]["literal"] is False


def test_send_agent_key_literal_text(fake_tmux: FakeTmux) -> None:
    agent = SimpleNamespace(session="murder_demo_crow_t001")
    rt = SimpleNamespace(get_agent=lambda _agent_id: agent)
    orch = Orchestrator(rt)

    result = asyncio.run(orch.send_agent_key("crow-t001", "x", literal=True))

    assert result["literal"] is True
    assert fake_tmux.calls_to("send_keys")[0][1]["literal"] is True
