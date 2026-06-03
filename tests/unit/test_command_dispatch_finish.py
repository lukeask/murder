"""``finish`` must surface a handler's soft-error instead of masking it.

When a handler returns ``{"handled": False, "error": "no agent named X"}`` the
dispatcher used to overwrite that with a generic "worker did not handle" string,
so the TUI toast hid the real reason a murda failed.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from murder.bus.protocol import CommandEvent
from murder.service.command_dispatch import CommandDispatcher


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


def test_finish_surfaces_handler_error() -> None:
    dispatcher, failures = _dispatcher()
    dispatcher.finish(
        command_id="cmd",
        command=_command(),
        worker_name="orchestrator",
        result={"handled": False, "error": "no agent named codex-rogue-x"},
    )
    assert failures == [("no agent named codex-rogue-x", False)]


def test_finish_falls_back_to_generic_when_no_error() -> None:
    dispatcher, failures = _dispatcher()
    dispatcher.finish(
        command_id="cmd",
        command=_command(),
        worker_name="orchestrator",
        result={"handled": False},
    )
    assert failures == [("worker 'orchestrator' did not handle 'agent.stop'", False)]


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
