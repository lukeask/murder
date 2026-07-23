"""Orchestrator raw-key delivery to harness tmux sessions."""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from murder.llm.harness_control.runtime.session import VerifiedHarnessControlSession
from murder.llm.harnesses.results import fail_result, ok_result
from murder.runtime.orchestration import agent_ops
from murder.runtime.orchestration.orchestrator import Orchestrator
from murder.runtime.terminal import tmux
from murder.state.persistence.agents import get_agent_messages
from murder.state.persistence.schema import get_db, init_db
from tests.support.fake_tmux import FakeTmux

MANUAL_ENTER_EFFECT_COUNT = 2


def _verified_agent(repo_root: Path, session_name: str) -> tuple[SimpleNamespace, object]:
    db = get_db(repo_root / ".murder" / "murder.db")
    init_db(db)
    control = VerifiedHarnessControlSession.from_tmux(
        harness_kind="codex",
        terminal_session=session_name,
        connection=db,
        persistence_session_id="crow-t001",
    )
    return SimpleNamespace(session=session_name, verified_harness_control=control), db


def test_send_agent_key_targets_agent_session(fake_tmux: FakeTmux, repo_root: Path) -> None:
    agent, _db = _verified_agent(repo_root, "murder_demo_crow_t001")
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
    assert result["terminal_transport_accepted"] is True
    assert result["harness_interpretation_verified"] is False
    assert fake_tmux.calls_to("send_keys") == [
        (("murder_demo_crow_t001", "Up"), {"literal": False, "enter": False}),
    ]


def test_send_agent_key_without_agent_id_ensures_collaborator(
    fake_tmux: FakeTmux,
    monkeypatch: pytest.MonkeyPatch,
    repo_root: Path,
) -> None:
    agent, _db = _verified_agent(repo_root, "murder_demo_collaborator")
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


def test_send_agent_key_literal_text(fake_tmux: FakeTmux, repo_root: Path) -> None:
    agent, _db = _verified_agent(repo_root, "murder_demo_crow_t001")
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, db = _verified_agent(repo_root, "murder_demo_crow_t001")
    rt = SimpleNamespace(
        get_agent=lambda _agent_id: agent,
        db=db,
        orchestration_events=None,
        run_id=None,
    )
    orch = Orchestrator(rt)

    # Physical emission is forbidden until the semantic action and every
    # lowered effect are durable. This assertion runs inside the fake terminal
    # call, proving ordering rather than merely inspecting the eventual rows.
    original_send_keys = tmux.send_keys

    async def _send_keys_after_durable_action(*args: object, **kwargs: object) -> None:
        assert db.execute("SELECT COUNT(*) FROM harness_control_actions").fetchone()[0] == 1
        assert (
            db.execute("SELECT COUNT(*) FROM harness_control_effects").fetchone()[0]
            == MANUAL_ENTER_EFFECT_COUNT
        )
        await original_send_keys(*args, **kwargs)

    monkeypatch.setattr(tmux, "send_keys", _send_keys_after_durable_action)

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
        (("murder_demo_crow_t001", "2"), {"literal": True, "enter": False}),
        (("murder_demo_crow_t001", "Enter"), {"literal": False, "enter": False}),
    ]
    operation = db.execute(
        "SELECT capability, status, phase_type FROM harness_control_operations "
        "WHERE operation_id = ?",
        (result["operation_id"],),
    ).fetchone()
    assert tuple(operation) == (
        "manual_terminal_input",
        "RUNNING",
        "murder.llm.harness_control.runtime.manual_input.ManualInputPhase",
    )
    action = db.execute(
        "SELECT duplicate_policy, emission_status FROM harness_control_actions WHERE action_id = ?",
        (result["action_id"],),
    ).fetchone()
    assert tuple(action) == ("NEVER_AUTOMATICALLY_REPLAY", "EMITTED")
    assert get_agent_messages(db, "crow-t001") == [
        {
            "ordinal": 0,
            "role": "user",
            "body": "2. No, exit",
            "captured_at": get_agent_messages(db, "crow-t001")[0]["captured_at"],
        }
    ]


def test_send_agent_key_has_no_direct_tmux_emitter() -> None:
    """The orchestration layer may inspect tmux state but cannot write input."""

    assert "tmux.send_keys" not in inspect.getsource(agent_ops)


def test_send_agent_key_rejects_missing_verified_control() -> None:
    agent = SimpleNamespace(session="murder_demo_crow_t001")
    rt = SimpleNamespace(get_agent=lambda _agent_id: agent)

    result = asyncio.run(Orchestrator(rt).send_agent_key("crow-t001", "Up"))

    assert result == {
        "ok": False,
        "error": "agent crow-t001 has no initialized verified harness control",
    }


def test_send_agent_message_reports_delivery_failure_without_user_block(
    repo_root: Path,
) -> None:
    db = get_db(repo_root / ".murder" / "murder.db")
    init_db(db)

    class _Agent:
        async def send(self, _message: str):
            return fail_result("Harness not awaiting input in time: session=crow-t001")

    rt = SimpleNamespace(
        get_agent=lambda agent_id: _Agent() if agent_id == "crow-t001" else None,
        get_crow_handler=lambda _ticket_id: None,
        db=db,
        orchestration_events=None,
        run_id=None,
    )
    orch = Orchestrator(rt)

    result = asyncio.run(orch.send_agent_message("crow-t001", "hello", None))

    assert result == {
        "ok": False,
        "error": "Harness not awaiting input in time: session=crow-t001",
    }
    assert get_agent_messages(db, "crow-t001") == []


def test_send_agent_message_records_user_block_after_delivery_acceptance(
    repo_root: Path,
) -> None:
    db = get_db(repo_root / ".murder" / "murder.db")
    init_db(db)

    class _Agent:
        async def send(self, _message: str):
            return ok_result()

    rt = SimpleNamespace(
        get_agent=lambda agent_id: _Agent() if agent_id == "crow-t001" else None,
        get_crow_handler=lambda _ticket_id: None,
        db=db,
        orchestration_events=None,
        run_id=None,
    )
    orch = Orchestrator(rt)

    result = asyncio.run(orch.send_agent_message("crow-t001", "hello", None))

    assert result == {"handled": True, "queued": False}
    messages = get_agent_messages(db, "crow-t001")
    assert [m["body"] for m in messages] == ["hello"]
