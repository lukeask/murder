"""Unit tests for SessionService (Phase 1 runtime decomposition)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from murder.runtime.activity_executor import build_session_bound_executor
from murder.runtime.sessions.contracts import (
    HarnessSessionRecord,
    SessionCapabilities,
    SessionStatus,
    SessionTransport,
)
from murder.runtime.sessions.service import (
    SessionBackendKind,
    SessionIdentityConflictError,
    SessionService,
    TmuxSessionRegistration,
)
from murder.runtime.terminal.capture import CapturedTerminalFrame
from tests.support.database import open_test_repo_db


def _registration(
    session_id: UUID,
    *,
    kind: str = "document_editor",
    tmux_name: str = "murder_editor_test",
    backend: SessionBackendKind = SessionBackendKind.PLAIN_TMUX,
) -> TmuxSessionRegistration:
    return TmuxSessionRegistration(
        session_id=session_id,
        session_kind=kind,
        tmux_name=tmux_name,
        capabilities=SessionCapabilities(raw_terminal=True, interruptible=False),
        backend=backend,
    )


@pytest.fixture
def db(tmp_path: Path):
    connection = open_test_repo_db(tmp_path / "murder.db")
    yield connection
    connection.close()


@pytest.mark.asyncio
async def test_ensure_new_session_is_idempotent(db) -> None:
    async with SessionService.open(db) as sessions:
        session_id = uuid4()
        first = await sessions.ensure_persisted_tmux_session(_registration(session_id))
        second = await sessions.ensure_persisted_tmux_session(_registration(session_id))
        assert first is second
        record = sessions.store.get_session(session_id)
        assert record is not None
        assert record.harness == "document_editor"
        assert record.transport_ref == "murder_editor_test"
        assert record.status is SessionStatus.READY


@pytest.mark.asyncio
async def test_identity_conflict_on_live_mismatched_transport_ref(db) -> None:
    async with SessionService.open(db) as sessions:
        session_id = uuid4()
        await sessions.ensure_persisted_tmux_session(_registration(session_id))
        with pytest.raises(SessionIdentityConflictError, match="transport_ref"):
            await sessions.ensure_persisted_tmux_session(
                _registration(session_id, tmux_name="other_pane")
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        SessionStatus.STOPPING,
        SessionStatus.STOPPED,
        SessionStatus.FAILED,
        SessionStatus.LOST,
    ],
)
async def test_revive_terminal_statuses_refresh_transport_ref(db, status: SessionStatus) -> None:
    async with SessionService.open(db) as sessions:
        session_id = uuid4()
        stale = await sessions.ensure_persisted_tmux_session(
            _registration(session_id, tmux_name="old_pane")
        )
        existing = sessions.store.get_session(session_id)
        assert existing is not None
        sessions.store.save_session(
            existing.model_copy(
                update={"status": status, "revision": existing.revision + 1}
            ),
            expected_revision=existing.revision,
        )
        revived_controller = await sessions.ensure_persisted_tmux_session(
            _registration(session_id, tmux_name="new_pane")
        )
        revived = sessions.store.get_session(session_id)
        assert revived is not None
        assert revived.status is SessionStatus.READY
        assert revived.transport_ref == "new_pane"
        assert revived.revision == existing.revision + 2
        # Stale controller/backend must not survive revival with a new transport_ref.
        assert revived_controller is not stale
        assert revived_controller._backend._session == "new_pane"  # noqa: SLF001


@pytest.mark.asyncio
async def test_harness_and_editor_registrations_converge(db) -> None:
    """Equivalent registrations produce the same persisted shape and controller."""

    session_id = uuid4()
    caps = SessionCapabilities(raw_terminal=True, interruptible=False)
    async with SessionService.open(db) as sessions:
        first = await sessions.ensure_persisted_tmux_session(
            TmuxSessionRegistration(
                session_id=session_id,
                session_kind="document_editor",
                tmux_name="shared_pane",
                capabilities=caps,
                backend=SessionBackendKind.PLAIN_TMUX,
            )
        )
        second = await sessions.ensure_persisted_tmux_session(
            TmuxSessionRegistration(
                session_id=session_id,
                session_kind="document_editor",
                tmux_name="shared_pane",
                capabilities=caps,
                backend=SessionBackendKind.PLAIN_TMUX,
            )
        )
        assert first is second
        record = sessions.store.get_session(session_id)
        assert record is not None
        assert record.harness == "document_editor"
        assert record.transport is SessionTransport.TMUX
        assert record.transport_ref == "shared_pane"
        assert record.capabilities == caps
        assert record.status is SessionStatus.READY
        with pytest.raises(SessionIdentityConflictError, match="session kind"):
            await sessions.ensure_persisted_tmux_session(
                _registration(session_id, kind="codex", tmux_name="shared_pane")
            )


@pytest.mark.asyncio
async def test_controller_reconstructed_from_persisted_tmux_without_caller_backend(
    db,
) -> None:
    async with SessionService.open(db) as sessions:
        session_id = uuid4()
        await sessions.ensure_persisted_tmux_session(_registration(session_id))
        # Drop the in-memory controller and rebuild from the store + backend factory.
        await sessions.controllers.remove(session_id)
        rebuilt = await sessions.controllers.get_or_create(session_id)
        assert rebuilt.session_id == session_id


@pytest.mark.asyncio
async def test_activity_executor_and_session_service_share_controller(db) -> None:
    async with SessionService.open(db) as sessions:
        session_id = uuid4()
        via_service = await sessions.ensure_persisted_tmux_session(_registration(session_id))
        registry = sessions.controllers
        executor = build_session_bound_executor(registry=registry)
        # §6.10: activity-executor path must close over SessionService's registry
        # (not construct a second one), so controller identity is preserved.
        closed = {
            name: cell.cell_contents
            for name, cell in zip(
                executor.__code__.co_freevars,
                executor.__closure__ or (),
                strict=True,
            )
        }
        assert closed["registry"] is registry
        via_executor = await registry.get_or_create(session_id, recover=False)
        assert via_service is via_executor


@pytest.mark.asyncio
async def test_resolve_tmux_ref_rejects_non_tmux(db) -> None:
    async with SessionService.open(db) as sessions:
        session_id = uuid4()
        now = datetime.now(timezone.utc)
        sessions.store.save_session(
            HarnessSessionRecord(
                session_id=session_id,
                repository_id=UUID(db.repository_id),
                harness="codex",
                transport=SessionTransport.APP_SERVER,
                transport_ref="app://1",
                status=SessionStatus.READY,
                revision=0,
                capabilities=SessionCapabilities(),
                started_at=now,
            )
        )
        with pytest.raises(ValueError, match="does not expose a tmux terminal"):
            sessions.resolve_tmux_ref(session_id)


@pytest.mark.asyncio
async def test_capture_uses_resolved_transport(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_capture(tmux_name: str) -> CapturedTerminalFrame:
        return CapturedTerminalFrame(data=f"frame:{tmux_name}", columns=80, rows=24)

    monkeypatch.setattr(
        "murder.runtime.sessions.service.capture_tmux_frame",
        _fake_capture,
    )
    async with SessionService.open(db) as sessions:
        session_id = uuid4()
        await sessions.ensure_persisted_tmux_session(
            _registration(session_id, tmux_name="pane_a")
        )
        frame = await sessions.capture_terminal(session_id)
        assert frame.data == "frame:pane_a"


@pytest.mark.asyncio
async def test_open_terminal_output_resolves_persisted_tmux_ref(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    from murder.runtime.terminal.output import TmuxTerminalOutput

    started: list[str] = []

    async def _fake_start(self: TmuxTerminalOutput) -> None:
        started.append(self.session_name)

    monkeypatch.setattr(TmuxTerminalOutput, "start", _fake_start)

    async with SessionService.open(db) as sessions:
        session_id = uuid4()
        await sessions.ensure_persisted_tmux_session(
            _registration(session_id, tmux_name="pane_out")
        )
        first = await sessions.open_terminal_output(session_id)
        second = await sessions.open_terminal_output(session_id)
        assert first is second
        assert first.session_name == "pane_out"
        assert started == ["pane_out"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        SessionStatus.STOPPING,
        SessionStatus.STOPPED,
        SessionStatus.FAILED,
        SessionStatus.LOST,
    ],
)
async def test_revive_replaces_stale_terminal_output_reader(
    db, monkeypatch: pytest.MonkeyPatch, status: SessionStatus
) -> None:
    from murder.runtime.terminal.output import TmuxTerminalOutput

    started: list[str] = []
    closed: list[str] = []

    async def _fake_start(self: TmuxTerminalOutput) -> None:
        started.append(self.session_name)

    async def _fake_close(self: TmuxTerminalOutput) -> None:
        closed.append(self.session_name)
        self._closed = True  # noqa: SLF001

    monkeypatch.setattr(TmuxTerminalOutput, "start", _fake_start)
    monkeypatch.setattr(TmuxTerminalOutput, "close", _fake_close)

    async with SessionService.open(db) as sessions:
        session_id = uuid4()
        await sessions.ensure_persisted_tmux_session(
            _registration(session_id, tmux_name="old_pane")
        )
        stale = await sessions.open_terminal_output(session_id)
        assert stale.session_name == "old_pane"

        existing = sessions.store.get_session(session_id)
        assert existing is not None
        sessions.store.save_session(
            existing.model_copy(
                update={"status": status, "revision": existing.revision + 1}
            ),
            expected_revision=existing.revision,
        )
        await sessions.ensure_persisted_tmux_session(
            _registration(session_id, tmux_name="new_pane")
        )
        # Revival must detach the old control client before any reopen.
        assert "old_pane" in closed
        assert stale.closed

        revived = await sessions.open_terminal_output(session_id)
        assert revived is not stale
        assert revived.session_name == "new_pane"
        assert started == ["old_pane", "new_pane"]


@pytest.mark.asyncio
async def test_open_terminal_output_replaces_reader_when_tmux_name_changes(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registry open must not cache-hit across a transport_ref rename."""

    from murder.runtime.terminal.output import TmuxTerminalOutput

    async def _fake_start(self: TmuxTerminalOutput) -> None:
        return None

    async def _fake_close(self: TmuxTerminalOutput) -> None:
        self._closed = True  # noqa: SLF001

    monkeypatch.setattr(TmuxTerminalOutput, "start", _fake_start)
    monkeypatch.setattr(TmuxTerminalOutput, "close", _fake_close)

    async with SessionService.open(db) as sessions:
        session_id = uuid4()
        await sessions.ensure_persisted_tmux_session(
            _registration(session_id, tmux_name="pane_a")
        )
        first = await sessions.open_terminal_output(session_id)
        # Bypass ensure_ revival: call the registry with a new name directly.
        second = await sessions.outputs.open(session_id, tmux_name="pane_b")
        assert first.closed
        assert second is not first
        assert second.session_name == "pane_b"


@pytest.mark.asyncio
async def test_capture_and_open_raise_after_close(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_capture(tmux_name: str) -> CapturedTerminalFrame:
        return CapturedTerminalFrame(data=f"frame:{tmux_name}", columns=80, rows=24)

    monkeypatch.setattr(
        "murder.runtime.sessions.service.capture_tmux_frame",
        _fake_capture,
    )

    async with SessionService.open(db) as sessions:
        session_id = uuid4()
        await sessions.ensure_persisted_tmux_session(
            _registration(session_id, tmux_name="pane_closed")
        )
        await sessions.close()
        with pytest.raises(RuntimeError, match="SessionService is closed"):
            await sessions.capture_terminal(session_id)
        with pytest.raises(RuntimeError, match="SessionService is closed"):
            await sessions.open_terminal_output(session_id)


@pytest.mark.asyncio
async def test_open_terminal_output_rejects_unknown_session(db) -> None:
    async with SessionService.open(db) as sessions:
        with pytest.raises(ValueError, match="does not exist"):
            await sessions.open_terminal_output(uuid4())


@pytest.mark.asyncio
async def test_harness_bootstrap_and_session_service_share_controller(db) -> None:
    """§6.10: harness ensure_session_controller must reuse SessionService registry."""

    from uuid import NAMESPACE_URL, uuid5

    from murder.llm.harness_control.runtime.session import VerifiedHarnessControlSession
    from murder.runtime.sessions.backend import VerifiedHarnessSessionBackend

    async with SessionService.open(db) as sessions:
        agent_key = "crow-t001"
        session_id = uuid5(
            NAMESPACE_URL,
            f"murder:harness-session:{UUID(db.repository_id)}:{agent_key}",
        )
        control = VerifiedHarnessControlSession.from_tmux(
            harness_kind="codex",
            terminal_session="murder_test_crow_t001",
            db=db,
            persistence_session_id=agent_key,
        )
        via_harness = await control.ensure_session_controller(
            repository_id=db.repository_id,
            agent_key=agent_key,
            sessions=sessions,
        )
        via_service = await sessions.controllers.get_or_create(session_id)
        assert via_harness is via_service
        assert control._session_controller is via_service
        assert isinstance(via_harness._backend, VerifiedHarnessSessionBackend)


@pytest.mark.asyncio
async def test_close_shuts_outputs_before_controllers(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []

    async with SessionService.open(db) as sessions:
        session_id = uuid4()
        await sessions.ensure_persisted_tmux_session(
            _registration(session_id, tmux_name="pane_close")
        )

        original_output_close = sessions.outputs.close
        original_controller_close = sessions.controllers.close

        async def _outputs_close() -> None:
            order.append("outputs")
            await original_output_close()

        async def _controllers_close() -> None:
            order.append("controllers")
            await original_controller_close()

        monkeypatch.setattr(sessions.outputs, "close", _outputs_close)
        monkeypatch.setattr(sessions.controllers, "close", _controllers_close)
        await sessions.close()

    assert order == ["outputs", "controllers"]
