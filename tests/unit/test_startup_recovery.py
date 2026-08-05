"""Phase 4: startup recovery is a one-shot boot workflow."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from murder.app.service.recovery import ReconcileReport
from murder.app.service.startup_recovery import (
    CrowReattachment,
    StartupRecoveryResult,
    run_startup_recovery,
)
from murder.state.persistence.connection import open_repo_db


def test_startup_recovery_returns_surviving_crows_and_kills_stale(
    fake_tmux, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del fake_tmux
    db = open_repo_db(repo_root)
    killed: list[str] = []

    report = ReconcileReport(
        agents_marked_dead=["crow-gone"],
        tickets_reset_to_failed=["t-gone"],
        sessions_to_kill=["stale-session"],
        harness_sessions_marked_lost=[str(uuid4())],
        crows_to_reattach=[("t-live", "crow-t-live")],
    )

    monkeypatch.setattr(
        "murder.app.service.startup_recovery.reconcile_agents_vs_tmux",
        lambda *_a, **_k: report,
    )
    monkeypatch.setattr(
        "murder.app.service.startup_recovery.tmux.list_sessions",
        AsyncMock(return_value=["crow-t-live", "stale-session"]),
    )

    async def _kill(name: str) -> None:
        killed.append(name)

    monkeypatch.setattr(
        "murder.app.service.startup_recovery.tmux.kill_session",
        _kill,
    )
    monkeypatch.setattr(
        "murder.app.service.startup_recovery.mark_stale_conversations",
        lambda _db: 2,
    )
    claims = MagicMock()
    reservations = MagicMock()
    monkeypatch.setattr(
        "murder.app.service.startup_recovery.reap_expired_claims",
        claims,
    )
    monkeypatch.setattr(
        "murder.app.service.startup_recovery.reap_expired_reservations",
        reservations,
    )
    recover_signals = MagicMock()
    monkeypatch.setattr(
        "murder.app.service.startup_recovery.WorkflowRuntime",
        lambda _db: MagicMock(recover_pending_signals=recover_signals),
    )

    async def _drive() -> StartupRecoveryResult:
        return await run_startup_recovery(db=db)

    result = asyncio.run(_drive())
    db.close()

    recover_signals.assert_called_once_with()
    claims.assert_called_once_with(db)
    reservations.assert_called_once_with(db)
    assert killed == ["stale-session"]
    assert result.agents_marked_dead == ("crow-gone",)
    assert result.tickets_reset_to_failed == ("t-gone",)
    assert result.sessions_killed == ("stale-session",)
    assert len(result.harness_sessions_marked_lost) == 1
    assert result.crows_to_reattach == (
        CrowReattachment(ticket_id="t-live", crow_session="crow-t-live"),
    )
    assert result.stale_conversations_marked == 2
    assert result


def test_startup_recovery_marks_lost_sessions_from_reconcile_report(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = open_repo_db(repo_root)
    lost_id = uuid4()
    report = ReconcileReport(harness_sessions_marked_lost=[str(lost_id)])
    monkeypatch.setattr(
        "murder.app.service.startup_recovery.reconcile_agents_vs_tmux",
        lambda *_a, **_k: report,
    )
    monkeypatch.setattr(
        "murder.app.service.startup_recovery.tmux.list_sessions",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "murder.app.service.startup_recovery.tmux.kill_session",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "murder.app.service.startup_recovery.mark_stale_conversations",
        lambda _db: 0,
    )
    monkeypatch.setattr(
        "murder.app.service.startup_recovery.reap_expired_claims",
        lambda _db: None,
    )
    monkeypatch.setattr(
        "murder.app.service.startup_recovery.reap_expired_reservations",
        lambda _db: None,
    )
    monkeypatch.setattr(
        "murder.app.service.startup_recovery.WorkflowRuntime",
        lambda _db: MagicMock(recover_pending_signals=lambda: None),
    )

    result = asyncio.run(run_startup_recovery(db=db))
    db.close()
    assert result.harness_sessions_marked_lost == (lost_id,)
    assert result.sessions_killed == ()
    assert result.crows_to_reattach == ()


def test_crow_reattachment_unpacks_like_tuple() -> None:
    crow = CrowReattachment(ticket_id="t1", crow_session="crow-t1")
    ticket_id, session = crow
    assert ticket_id == "t1"
    assert session == "crow-t1"
