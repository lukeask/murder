"""CompletionCoordinator: FAIL_TICKET short-circuits the failures loop and reprompt."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import murder.verdict.completion.coordinator as coord_mod
from murder.verdict.completion.checks.base import CheckResult, CheckStatus, CompletionContext
from murder.verdict.completion.coordinator import CompletionCoordinator, DoneHandleResult
from murder.verdict.completion.policy import Owner


class _StubCheck:
    def __init__(self, name: str) -> None:
        self.name = name
        self.ran = False

    async def run(self, ctx: CompletionContext) -> CheckResult:
        self.ran = True
        return CheckResult(status=CheckStatus.FAIL, message=f"{self.name} failed")


@pytest.fixture
def patched(monkeypatch):
    # Neutralize the module-level persistence helpers the coordinator calls.
    monkeypatch.setattr(coord_mod, "get_attempts", lambda *a, **k: 0)
    monkeypatch.setattr(coord_mod, "bump_attempts", lambda *a, **k: None)
    monkeypatch.setattr(coord_mod, "reset_attempts", lambda *a, **k: None)
    monkeypatch.setattr(coord_mod, "write_check_result", lambda *a, **k: None)
    # get_ticket is imported inside handle_done from murder.state.persistence.tickets.
    monkeypatch.setattr(
        "murder.state.persistence.tickets.get_ticket",
        lambda _conn, _tid: {"id": _tid},
    )


def _make_coordinator(checks):
    rt = MagicMock()
    rt.db = MagicMock()
    rt.repo_root = Path("/tmp")
    rt.orchestration_events = None
    rt.run_id = None
    crow = MagicMock()
    crow.send = AsyncMock()
    rt.get_crow = MagicMock(return_value=crow)

    registry = MagicMock()
    registry.assigned_checks = MagicMock(return_value=list(checks))

    coordinator = CompletionCoordinator(rt, registry)
    return coordinator, rt, crow


def test_fail_ticket_short_circuits_reprompt_and_remaining_checks(patched, monkeypatch):
    first = _StubCheck("first")  # -> FAIL_TICKET
    second = _StubCheck("second")  # -> REPROMPT (must never dispatch)
    coordinator, rt, crow = _make_coordinator([first, second])

    # Map resolution by check name so the order is deterministic.
    def _policy(name: str, _attempts: int) -> Owner:
        return Owner.FAIL_TICKET if name == "first" else Owner.REPROMPT

    monkeypatch.setattr(coord_mod, "resolution_policy", _policy)

    fail_ticket = AsyncMock()
    monkeypatch.setattr(coordinator, "_fail_ticket", fail_ticket)

    result = asyncio.run(
        coordinator.handle_done("t001", crow_session="crow-t001", repo_root=Path("/tmp"))
    )

    # Both checks RUN (results are gathered before dispatch), but only the first
    # is DISPATCHED; FAIL_TICKET breaks before the second's dispatch.
    fail_ticket.assert_awaited_once()
    # The reprompt to the crow must never be sent for a failed ticket.
    crow.send.assert_not_called()
    assert isinstance(result, DoneHandleResult)
    assert result.completed is False
    # failed_checks remains the full failures tuple.
    assert set(result.failed_checks) == {"first", "second"}


def test_dispatch_returns_true_only_for_fail_ticket(patched):
    coordinator, rt, crow = _make_coordinator([])
    monkeypatch_fail = AsyncMock()
    coordinator._fail_ticket = monkeypatch_fail  # type: ignore[method-assign]
    coordinator._ask_planner = AsyncMock()  # type: ignore[method-assign]
    coordinator._escalate_to_user = AsyncMock()  # type: ignore[method-assign]
    coordinator._block_ticket = AsyncMock()  # type: ignore[method-assign]

    res = CheckResult(status=CheckStatus.FAIL, message="x")
    msgs: list[str] = []

    async def _call(owner):
        return await coordinator._dispatch(
            owner,
            ticket_id="t001",
            check_name="c",
            result=res,
            crow_session="crow-t001",
            reprompt_msgs=msgs,
        )

    assert asyncio.run(_call(Owner.FAIL_TICKET)) is True
    assert asyncio.run(_call(Owner.REPROMPT)) is False
    assert asyncio.run(_call(Owner.ASK_PLANNER)) is False
    assert asyncio.run(_call(Owner.ASK_USER)) is False
