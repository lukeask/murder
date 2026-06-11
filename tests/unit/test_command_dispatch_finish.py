"""``finish`` implements a three-way command result contract.

- ``{"handled": False}`` → wiring miss → fail with a generic "did not handle"
  message (a routing bug; logged at ERROR).
- ``{"ok": False, "error": ...}`` → domain failure → fail and surface the
  handler's own error string.
- success (no ``handled: False`` / ``ok: False``) → complete.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest

from murder.bus.protocol import CommandEvent
from murder.app.service.command_dispatch import CommandDispatcher, command_from_row


def _command(kind: str = "agent.stop") -> CommandEvent:
    return CommandEvent(
        id=uuid4(),
        run_id="run",
        agent_id="",
        role=None,
        ticket_id=None,
        target_worker="orchestrator",
        kind=kind,
        payload={},
        correlation_id="c",
        idempotency_key="i",
    )


def _dispatcher() -> tuple[CommandDispatcher, list[tuple[str, bool]]]:
    dispatcher = CommandDispatcher(conn=None, repo_root=Path("."))  # type: ignore[arg-type]
    failures: list[tuple[str, bool]] = []
    completions: list[object] = []
    dispatcher.fail = lambda command_id, last_error, *, retryable=True: failures.append(  # type: ignore[method-assign]
        (last_error, retryable)
    )
    dispatcher.complete = lambda command_id, result: completions.append(result)  # type: ignore[method-assign]
    dispatcher._completions = completions  # type: ignore[attr-defined]
    return dispatcher, failures


def test_finish_wiring_miss_uses_generic_message() -> None:
    dispatcher, failures = _dispatcher()
    dispatcher.finish(
        command_id="cmd",
        command=_command(),
        worker_name="orchestrator",
        result={"handled": False},
    )
    assert failures == [("worker 'orchestrator' did not handle 'agent.stop'", False)]
    assert dispatcher._completions == []  # type: ignore[attr-defined]


def test_finish_wiring_miss_ignores_any_error_field() -> None:
    # ``handled: False`` is a routing bug regardless of any ``error`` text the
    # worker may have attached; the generic message wins.
    dispatcher, failures = _dispatcher()
    dispatcher.finish(
        command_id="cmd",
        command=_command(),
        worker_name="orchestrator",
        result={"handled": False, "error": "ignored"},
    )
    assert failures == [("worker 'orchestrator' did not handle 'agent.stop'", False)]


def test_finish_domain_failure_surfaces_handler_error() -> None:
    dispatcher, failures = _dispatcher()
    dispatcher.finish(
        command_id="cmd",
        command=_command(),
        worker_name="orchestrator",
        result={"ok": False, "error": "no agent named codex-rogue-x"},
    )
    assert failures == [("no agent named codex-rogue-x", False)]
    assert dispatcher._completions == []  # type: ignore[attr-defined]


def test_finish_domain_failure_falls_back_to_generic_when_no_error() -> None:
    dispatcher, failures = _dispatcher()
    dispatcher.finish(
        command_id="cmd",
        command=_command(),
        worker_name="orchestrator",
        result={"ok": False},
    )
    assert failures == [("command 'agent.stop' failed", False)]


def test_finish_legacy_handled_true_ok_false_no_longer_completes() -> None:
    # The accidental ``{"handled": True, "ok": False}`` shape used to route to
    # complete(); now it is recognized as a domain failure.
    dispatcher, failures = _dispatcher()
    dispatcher.finish(
        command_id="cmd",
        command=_command(),
        worker_name="orchestrator",
        result={"handled": True, "ok": False, "error": "ticket not found"},
    )
    assert failures == [("ticket not found", False)]
    assert dispatcher._completions == []  # type: ignore[attr-defined]


def test_finish_completes_when_handled() -> None:
    dispatcher, failures = _dispatcher()
    dispatcher.finish(
        command_id="cmd",
        command=_command(),
        worker_name="orchestrator",
        result={"handled": True, "agent_id": "x"},
    )
    assert failures == []
    assert dispatcher._completions == [{"handled": True, "agent_id": "x"}]  # type: ignore[attr-defined]


def test_finish_completes_on_ok_true() -> None:
    dispatcher, failures = _dispatcher()
    dispatcher.finish(
        command_id="cmd",
        command=_command(),
        worker_name="orchestrator",
        result={"ok": True, "ticket_id": "t1"},
    )
    assert failures == []
    assert dispatcher._completions == [{"ok": True, "ticket_id": "t1"}]  # type: ignore[attr-defined]


def _row(row_id: str) -> dict[str, object]:
    return {
        "id": row_id,
        "run_id": "run",
        "agent_id": "",
        "role": None,
        "ticket_id": None,
        "target_worker": "orchestrator",
        "kind": "agent.stop",
        "payload_json": "{}",
        "correlation_id": "c",
        "idempotency_key": "i",
        "status": "pending",
        "claimed_by": None,
        "lease_expires_at": None,
        "attempt_count": 0,
        "retryable": 0,
        "result_json": None,
    }


def test_command_from_row_round_trips_valid_uuid() -> None:
    row_id = str(uuid4())
    event = command_from_row(_row(row_id))
    assert event.id == UUID(row_id)
    assert str(event.id) == row_id


def test_command_from_row_raises_on_non_uuid_id() -> None:
    with pytest.raises(ValueError, match="non-UUID id"):
        command_from_row(_row("not-a-uuid"))


def test_claim_next_quarantines_non_uuid_row() -> None:
    dispatcher, failures = _dispatcher()

    import murder.app.service.command_dispatch as mod

    original = mod.cmd_db.claim_next_command
    mod.cmd_db.claim_next_command = lambda *a, **k: _row("not-a-uuid")  # type: ignore[assignment]
    try:
        claimed = dispatcher.claim_next(
            target_worker="orchestrator", claimed_by="orchestrator"
        )
    finally:
        mod.cmd_db.claim_next_command = original  # type: ignore[assignment]

    assert claimed is None
    assert failures == [("non-UUID command id", False)]
