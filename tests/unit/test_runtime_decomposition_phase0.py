"""Phase 0 characterization for Runtime god-class decomposition.

Pins current behavior before extraction. No production behavior change.
See `.murder/reports/murder_runtime_decomposition_spec.md` §8 Phase 0.

Phase 0 coverage (seven areas):
  1. startup rollback
  2. shutdown preserve-versus-kill behavior
  3. document editor registration and terminal input
     (incl. STOPPING / transport_ref divergence vs harness path)
  4. terminal capture fallback behavior
  5. agent register/rename/reap persistence
     (incl. persist-failure index gaps Phase 2 closes)
  6. sync final reconciliation
  7. startup recovery report consumption

Off-protocol Runtime attributes (§6.6)
--------------------------------------
These are reached on the concrete Runtime by murder/runtime/ consumers but
appear on neither AgentLifecycleHost nor OrchestratorHost. Deleting the
protocols without threading them fails at runtime, not at type-check time.

  Attribute                      Declared on Runtime?    Consumers
  -----------------------------  ----------------------  ---------------------------
  roster                         Yes (nullable; start)   crow_handler, base (getattr)
  plan_sync                      Yes (nullable; start)   plan_ops
  user_cfg                       Yes (constructor)       plan_ops, note_ops,
                                                         orchestrator
  crow_ask_router                Yes (host late-bind)    crow_handler (getattr)
  verified_prompt_driver_policy  No (test monkeypatch)   agents/base.py
  verified_prompt_driver_sleep   No (test monkeypatch)   agents/base.py

Notes:
  - Spec §6.6 lists five rows; verified_prompt_driver_* is one row covering both.
  - Neither verified-prompt attribute is assigned in production Runtime.__init__ /
    start / ServiceHost; they exist so tests can override driver construction.
  - crow_ask_router is late-bound: ServiceHost._start_inner sets
    runtime.crow_ask_router = orchestrator.route_crow_ask after Orchestrator exists.
  - Later-phase seams: AgentRuntime.heartbeat; explicit PlanSync / UserConfig on
    Orchestrator; explicit crow_ask callback; VerifiedControlFactory.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from murder.app.service.background_tasks import ServiceBackgroundTasks
from murder.app.service.filesystem_sync import FilesystemSyncSupervisor
from murder.app.service.recovery import ReconcileReport
from murder.app.service.runtime import Runtime
from murder.config import (
    Config,
    CrowHandlerConfig,
    HarnessRoleConfig,
    ProjectConfig,
)
from murder.runtime.agents.types import AgentRole, AgentStatus
from murder.runtime.orchestration.runtime_scope import AgentLifecycleHost, OrchestratorHost
from murder.runtime.sessions.contracts import (
    AcquireWriterLease,
    Correlation,
    HarnessSessionRecord,
    PrincipalKind,
    PrincipalRef,
    RequestMeta,
    SessionCapabilities,
    SessionStatus,
    SessionTransport,
    WriterLeaseGranted,
    WriterMode,
    WriteTerminalInput,
)
from murder.runtime.sessions.persistence import SessionStore
from murder.state.storage.filesystem import lock_is_held
from murder.state.storage.paths import lock_path

# §6.6 five off-protocol seams (verified_prompt_driver_* counted as one row in the
# spec table, but both getattr names must be threaded before protocol deletion).
_OFF_PROTOCOL_RUNTIME_ATTRS: tuple[str, ...] = (
    "roster",
    "plan_sync",
    "user_cfg",
    "crow_ask_router",
    "verified_prompt_driver_policy",
    "verified_prompt_driver_sleep",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config() -> Config:
    return Config(
        project=ProjectConfig(name="repo"),
        collaborator=HarnessRoleConfig(harness="codex"),
        default_crow=HarnessRoleConfig(harness="codex"),
        crow_handler=CrowHandlerConfig(model="test-model"),
    )


class _RecordingAgent:
    """Minimal LifecycleParticipant stand-in for register/rename/reap/stop."""

    def __init__(
        self,
        agent_id: str,
        *,
        role: AgentRole = AgentRole.CROW,
        ticket_id: str | None = "t1",
        status: AgentStatus = AgentStatus.RUNNING,
    ) -> None:
        self.id = agent_id
        self.role = role
        self.ticket_id = ticket_id
        self.session = f"sess-{agent_id}"
        self.harness = SimpleNamespace(kind="codex")
        self.startup_model = "test-model"
        self.status = status
        self.start_commit = None
        self.worktree_path = None
        self.stop_calls: list[dict[str, object]] = []

    async def stop(self, *, failed: bool = True, kill_session: bool = True) -> None:
        self.stop_calls.append({"failed": failed, "kill_session": kill_session})


class _SpySupervisor:
    """Records FilesystemSyncSupervisor boot/shutdown call sequence."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.plan_sync = MagicMock()
        self.note_sync = MagicMock()
        self.notetaker_context_sync = MagicMock()
        self.ticket_sync = MagicMock()
        self.report_sync = MagicMock()

    def seed(self) -> None:
        self.calls.append("seed")

    def spawn_tasks(self) -> dict[str, asyncio.Task[None]]:
        self.calls.append("spawn_tasks")
        return {}

    async def reconcile_all(self) -> None:
        self.calls.append("reconcile_all")

    async def shutdown(self, tasks: dict[str, asyncio.Task[None]]) -> None:
        self.calls.append("shutdown")
        await self.reconcile_all()


def _install_spy_sync(monkeypatch: pytest.MonkeyPatch) -> _SpySupervisor:
    spy = _SpySupervisor()

    def _attach(*_args, **_kwargs) -> _SpySupervisor:
        return spy

    monkeypatch.setattr(
        "murder.app.service.runtime.FilesystemSyncSupervisor.attach",
        _attach,
    )
    return spy


def _patch_editor_tmux_kwargs(monkeypatch: pytest.MonkeyPatch, fake_tmux) -> None:
    """FakeTmux.create_session rejects width/height; editors pass them."""

    async def _create_session(
        name: str,
        cwd: object,
        cmd: list[str] | None = None,
        *,
        width: int = 220,
        height: int = 50,
    ) -> None:
        fake_tmux.calls.append(
            ("create_session", (name, cwd, list(cmd or [])), {"width": width, "height": height})
        )
        fake_tmux._sessions.add(name)

    async def _resize_session(name: str, *, columns: int, rows: int) -> None:
        fake_tmux.calls.append(("resize_session", (name,), {"columns": columns, "rows": rows}))

    monkeypatch.setattr(
        "murder.runtime.terminal.tmux.create_session",
        _create_session,
    )
    monkeypatch.setattr(
        "murder.runtime.terminal.tmux.resize_session",
        _resize_session,
    )


def _write_plan(repo_root: Path, name: str = "safe") -> Path:
    document = repo_root / ".murder" / "plans" / f"{name}.md"
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(f"# {name}\n")
    return document


# ---------------------------------------------------------------------------
# §6.6 off-protocol attribute inventory (commit-local; .murder/ is gitignored)
# ---------------------------------------------------------------------------


def _host_protocol_member_names() -> frozenset[str]:
    names: set[str] = set()
    for base in (AgentLifecycleHost, OrchestratorHost):
        names.update(getattr(base, "__annotations__", {}))
        names.update(
            name
            for name, value in vars(base).items()
            if not name.startswith("_") and callable(value)
        )
    return frozenset(names)


# Consumer modules that reach off-protocol Runtime attributes today (§6.6).
# Keep this list in sync with the module docstring inventory above.
_OFF_PROTOCOL_CONSUMERS: dict[str, tuple[str, ...]] = {
    "roster": (
        "murder/runtime/agents/crow_handler.py",
        "murder/runtime/agents/base.py",
    ),
    "plan_sync": ("murder/runtime/orchestration/plan_ops.py",),
    "user_cfg": (
        "murder/runtime/orchestration/plan_ops.py",
        "murder/runtime/orchestration/note_ops.py",
        "murder/runtime/orchestration/orchestrator.py",
    ),
    "crow_ask_router": ("murder/runtime/agents/crow_handler.py",),
    "verified_prompt_driver_policy": ("murder/runtime/agents/base.py",),
    "verified_prompt_driver_sleep": ("murder/runtime/agents/base.py",),
}


def test_off_protocol_runtime_attributes_absent_from_host_protocols(
    repo_root: Path,
) -> None:
    """Concrete Runtime is wider than AgentLifecycleHost / OrchestratorHost.

    Production consumers reach these attributes only because the object passed
    is always Runtime. Protocol deletion without threading them is a runtime
    failure, not a type error.
    """
    rt = Runtime(_config(), repo_root)
    protocol_members = _host_protocol_member_names()

    for name in _OFF_PROTOCOL_RUNTIME_ATTRS:
        assert name not in protocol_members, f"{name} unexpectedly on a host protocol"

    # Declared on Runtime today (nullable until start / host wiring).
    assert hasattr(rt, "roster")
    assert hasattr(rt, "plan_sync")
    assert hasattr(rt, "user_cfg")
    assert hasattr(rt, "crow_ask_router")
    # Test/monkeypatch-only; not assigned in production construction.
    assert not hasattr(rt, "verified_prompt_driver_policy")
    assert not hasattr(rt, "verified_prompt_driver_sleep")


def test_off_protocol_consumer_sites_still_reference_attributes() -> None:
    """Executable inventory: consumer files still mention each off-protocol name.

    Phase 2 must thread these before deleting runtime_scope protocols. If a
    consumer drops an attribute, update this map and the module docstring.
    """
    repo = Path(__file__).resolve().parents[2]
    for attr, rel_paths in _OFF_PROTOCOL_CONSUMERS.items():
        assert rel_paths, f"{attr} has no inventoried consumers"
        for rel in rel_paths:
            text = (repo / rel).read_text(encoding="utf-8")
            assert attr in text, f"{rel} no longer references off-protocol {attr!r}"


def test_phase05_runtime_scope_lives_under_runtime_not_app() -> None:
    """Phase 0.5: protocols relocated; no upward app.service.runtime_scope imports."""
    repo = Path(__file__).resolve().parents[2]
    assert not (repo / "murder/app/service/runtime_scope.py").exists()
    assert (repo / "murder/runtime/orchestration/runtime_scope.py").is_file()

    stale = "murder.app.service.runtime_scope"
    runtime_root = repo / "murder" / "runtime"
    offenders: list[str] = []
    for path in runtime_root.rglob("*.py"):
        if path.name == "runtime_scope.py":
            # Module docstring may mention the old path as relocation history.
            continue
        text = path.read_text(encoding="utf-8")
        if stale in text:
            offenders.append(str(path.relative_to(repo)))
    assert offenders == [], f"stale upward imports: {offenders}"


# ---------------------------------------------------------------------------
# Startup rollback
# ---------------------------------------------------------------------------


def test_startup_failure_after_flock_releases_flock(
    fake_tmux, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any failure inside start()'s try releases the repo flock."""
    monkeypatch.setattr(
        "murder.app.service.runtime.open_repo_db",
        lambda _root: (_ for _ in ()).throw(RuntimeError("db boom")),
    )
    rt = Runtime(_config(), repo_root)
    lock = lock_path(repo_root)

    async def _drive() -> None:
        with pytest.raises(RuntimeError, match="db boom"):
            await rt.start()

    asyncio.run(_drive())

    assert rt.db is None
    assert rt._lock_fd is None
    assert lock_is_held(lock) is False
    assert not lock.exists()


def test_startup_failure_after_db_closes_db_and_releases_flock(
    fake_tmux, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "murder.app.service.runtime.reconcile_agents_vs_tmux",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("reconcile boom")),
    )
    rt = Runtime(_config(), repo_root)
    lock = lock_path(repo_root)

    async def _drive() -> None:
        with pytest.raises(RuntimeError, match="reconcile boom"):
            await rt.start()

    asyncio.run(_drive())

    assert rt.db is None
    assert rt.session_controllers is None
    assert rt.sessions is None
    assert rt._lock_fd is None
    assert lock_is_held(lock) is False


def test_startup_failure_after_tasks_spawned_cancels_them(
    fake_tmux, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback cancels any tasks already placed in Runtime._tasks."""
    spawned: list[asyncio.Task[None]] = []

    async def _hang() -> None:
        await asyncio.Event().wait()

    class _HangingSupervisor(_SpySupervisor):
        def spawn_tasks(self) -> dict[str, asyncio.Task[None]]:
            self.calls.append("spawn_tasks")
            task = asyncio.create_task(_hang())
            spawned.append(task)
            return {"plan_sync": task}

    hanging = _HangingSupervisor()
    monkeypatch.setattr(
        "murder.app.service.runtime.FilesystemSyncSupervisor.attach",
        lambda *_a, **_k: hanging,
    )

    def _boom(_db, _registry=None):
        raise RuntimeError("dispatcher boom")

    rt = Runtime(_config(), repo_root, activity_dispatcher_factory=_boom)

    async def _drive() -> None:
        with pytest.raises(RuntimeError, match="dispatcher boom"):
            await rt.start()

    asyncio.run(_drive())

    assert rt._tasks == {}
    assert spawned and spawned[0].cancelled()
    assert rt.db is None
    assert lock_is_held(lock_path(repo_root)) is False


# ---------------------------------------------------------------------------
# Shutdown preserve-versus-kill
# ---------------------------------------------------------------------------


def test_authoritative_stop_kills_agent_sessions_and_sweeps_project_tmux(
    fake_tmux, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default stop (external_stop clear) is authoritative: kill_session=True + sweep."""
    _install_spy_sync(monkeypatch)
    killed_via_sweep: list[str] = []

    async def _sweep(scope: object) -> list[str]:
        del scope
        killed_via_sweep.append("swept")
        return ["murder_repo_crow_t1"]

    monkeypatch.setattr(
        "murder.app.service.runtime.kill_project_tmux_sessions",
        _sweep,
    )
    agent = _RecordingAgent("crow-t1", ticket_id=None, role=AgentRole.COLLABORATOR)
    rt = Runtime(_config(), repo_root)

    async def _drive() -> None:
        await rt.start()
        # In-memory only — shutdown policy does not require roster persistence.
        rt._agents.register(agent)
        # ServiceHost.stop() always clear_shutdown_signal() before runtime.stop().
        rt.clear_shutdown_signal()
        await rt.stop()

    asyncio.run(_drive())

    assert agent.stop_calls == [{"failed": True, "kill_session": True}]
    assert killed_via_sweep == ["swept"]


def test_graceful_stop_preserves_agent_tmux_but_still_sweeps_project_sessions(
    fake_tmux, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Latent policy split: graceful stop sets kill_session=False, yet sweep still runs.

    Spec §7.2: unreachable in production because ServiceHost clears the signal
    first; characterization pins the Runtime-level disagreement so Phase 4 can
    make the sweep conditional.
    """
    _install_spy_sync(monkeypatch)
    killed_via_sweep: list[str] = []

    async def _sweep(scope: object) -> list[str]:
        del scope
        killed_via_sweep.append("swept")
        return ["murder_repo_crow_t1"]

    monkeypatch.setattr(
        "murder.app.service.runtime.kill_project_tmux_sessions",
        _sweep,
    )
    agent = _RecordingAgent("crow-t1", ticket_id=None, role=AgentRole.COLLABORATOR)
    rt = Runtime(_config(), repo_root)

    async def _drive() -> None:
        await rt.start()
        rt._agents.register(agent)
        # Simulate signal-graceful path without going through ServiceHost.
        rt._external_stop.set()
        await rt.stop()

    asyncio.run(_drive())

    assert agent.stop_calls == [{"failed": True, "kill_session": False}]
    assert killed_via_sweep == ["swept"], "current Runtime.stop sweeps even on graceful stop"


# ---------------------------------------------------------------------------
# Document editor registration and terminal input
# ---------------------------------------------------------------------------


def test_start_document_editor_registers_persisted_session_and_controller(
    fake_tmux, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_spy_sync(monkeypatch)
    _patch_editor_tmux_kwargs(monkeypatch, fake_tmux)
    _write_plan(repo_root)
    monkeypatch.setenv("VISUAL", "/bin/true")
    rt = Runtime(_config(), repo_root)

    async def _drive() -> UUID:
        await rt.start()
        session, reused = await rt.start_document_editor("plan", "safe", 80, 24)
        assert reused is False
        assert rt.db is not None
        store = SessionStore(rt.db)
        record = store.get_session(session.session_id)
        assert record is not None
        assert record.harness == "document_editor"
        assert record.transport is SessionTransport.TMUX
        assert record.transport_ref == session.tmux_name
        assert record.status is SessionStatus.READY
        assert record.capabilities.raw_terminal is True
        assert record.capabilities.interruptible is False
        assert rt.session_controllers is not None
        controller = await rt.session_controllers.get_or_create(record)
        assert controller is not None
        assert not controller.closed
        await rt.stop()
        return session.session_id

    asyncio.run(_drive())


def test_document_editor_registration_revives_stopping_and_refreshes_transport_ref(
    fake_tmux, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 1 unified revival: STOPPING revives and transport_ref refreshes."""
    _install_spy_sync(monkeypatch)
    _patch_editor_tmux_kwargs(monkeypatch, fake_tmux)
    _write_plan(repo_root)
    monkeypatch.setenv("VISUAL", "/bin/true")
    rt = Runtime(_config(), repo_root)

    async def _drive() -> None:
        await rt.start()
        session, _ = await rt.start_document_editor("plan", "safe", 80, 24)
        assert rt.db is not None
        store = SessionStore(rt.db)
        existing = store.get_session(session.session_id)
        assert existing is not None

        stopping = existing.model_copy(
            update={"status": SessionStatus.STOPPING, "revision": existing.revision + 1}
        )
        store.save_session(stopping, expected_revision=existing.revision)

        _, reused = await rt.start_document_editor("plan", "safe", 80, 24)
        assert reused is True
        after = store.get_session(session.session_id)
        assert after is not None
        assert after.status is SessionStatus.READY
        assert after.revision == stopping.revision + 1
        assert after.transport_ref == session.tmux_name

        # STOPPED with a mismatched transport_ref is revived and refreshed.
        stopped = after.model_copy(
            update={
                "status": SessionStatus.STOPPED,
                "transport_ref": "stale_editor_pane",
                "revision": after.revision + 1,
            }
        )
        store.save_session(stopped, expected_revision=after.revision)
        await rt.start_document_editor("plan", "safe", 80, 24)
        revived = store.get_session(session.session_id)
        assert revived is not None
        assert revived.status is SessionStatus.READY
        assert revived.transport_ref == session.tmux_name

        await rt.stop()

    asyncio.run(_drive())


def test_document_editor_terminal_input_goes_through_session_controller(
    fake_tmux, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fenced raw input uses WriteTerminalInput on the controller."""
    _install_spy_sync(monkeypatch)
    _patch_editor_tmux_kwargs(monkeypatch, fake_tmux)
    _write_plan(repo_root)
    monkeypatch.setenv("VISUAL", "/bin/true")
    written: list[bytes] = []

    class _RecordingBackend:
        async def recover(self, record: HarnessSessionRecord) -> None:
            del record

        async def send_structured_message(self, command) -> None:
            del command

        async def write_terminal_input(self, command, data: bytes) -> None:
            del command
            written.append(data)

        async def resize_terminal(self, command) -> None:
            del command

        async def interrupt(self, command) -> None:
            del command

        async def terminate(self, command) -> None:
            del command

    rt = Runtime(_config(), repo_root)

    async def _drive() -> None:
        await rt.start()
        assert rt.session_controllers is not None
        original_get_or_create = rt.session_controllers.get_or_create

        async def _get_or_create(record, *, backend=None, recover=False):
            del recover
            return await original_get_or_create(record, backend=_RecordingBackend())

        monkeypatch.setattr(rt.session_controllers, "get_or_create", _get_or_create)

        session, _ = await rt.start_document_editor("plan", "safe", 80, 24)
        assert rt.db is not None
        store = SessionStore(rt.db)
        principal = PrincipalRef(kind=PrincipalKind.CLIENT, id="client-1")
        granted = store.acquire_writer_lease(
            AcquireWriterLease(
                meta=RequestMeta(
                    request_id=uuid4(),
                    correlation=Correlation(correlation_id=uuid4()),
                ),
                session_id=session.session_id,
                mode=WriterMode.RAW_TERMINAL,
            ),
            holder=principal,
        )
        assert isinstance(granted, WriterLeaseGranted)

        assert rt.sessions is not None
        controller = await rt.sessions.controllers.get_or_create(
            rt.sessions.store.get_session(session.session_id)
        )
        import base64
        from uuid import uuid4 as _uuid4

        await controller.execute(
            WriteTerminalInput(
                operation_id=_uuid4(),
                lease_id=granted.lease.lease_id,
                fence=granted.lease.fence,
                encoding="base64",
                data=base64.b64encode(b"hello").decode("ascii"),
            ),
            principal=principal,
        )
        assert not hasattr(rt, "document_editor_input")
        assert not hasattr(rt, "write_document_editor_terminal_input")
        await rt.stop()

    asyncio.run(_drive())

    assert written == [b"hello"]


# ---------------------------------------------------------------------------
# Terminal capture (single SessionService path)
# ---------------------------------------------------------------------------


def test_capture_terminal_uses_persisted_session_path(
    fake_tmux, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_spy_sync(monkeypatch)
    _patch_editor_tmux_kwargs(monkeypatch, fake_tmux)
    _write_plan(repo_root)
    monkeypatch.setenv("VISUAL", "/bin/true")
    fake_tmux._last_captured_pane = "editor-frame"
    fake_tmux.set_pane_dimensions(91, 27)

    rt = Runtime(_config(), repo_root)

    async def _drive() -> None:
        await rt.start()
        session, _ = await rt.start_document_editor("plan", "safe", 80, 24)

        # Editors register through SessionService, so capture goes through
        # the generic persisted path (no editor-first probe).
        assert rt.sessions is not None
        editor_frame = await rt.sessions.capture_terminal(session.session_id)
        assert editor_frame.data == "editor-frame"
        assert (editor_frame.columns, editor_frame.rows) == (91, 27)

        missing = uuid4()
        with pytest.raises(ValueError, match="does not exist"):
            await rt.sessions.capture_terminal(missing)

        harness_id = uuid4()
        assert rt.db is not None
        now = datetime.now(timezone.utc)
        SessionStore(rt.db).save_session(
            HarnessSessionRecord(
                session_id=harness_id,
                repository_id=UUID(rt.db.repository_id),
                harness="codex",
                transport=SessionTransport.TMUX,
                transport_ref="murder_harness_pane",
                status=SessionStatus.READY,
                revision=0,
                capabilities=SessionCapabilities(raw_terminal=True),
                started_at=now,
                last_observed_at=now,
            )
        )
        fake_tmux.add_session("murder_harness_pane")
        fake_tmux._last_captured_pane = "harness-frame"
        harness_frame = await rt.sessions.capture_terminal(harness_id)
        assert harness_frame.data == "harness-frame"

        assert not hasattr(rt, "capture_terminal_frame")
        await rt.stop()

    asyncio.run(_drive())


# ---------------------------------------------------------------------------
# Agent register / rename / reap persistence
# ---------------------------------------------------------------------------


def _insert_ticket(db, ticket_id: str) -> None:
    db.conn.execute(
        "INSERT INTO tickets(repository_id, id, title, status, created_at, updated_at) "
        "VALUES (?, ?, ?, 'in_progress', '2026-01-01', '2026-01-01')",
        (db.repository_id, ticket_id, f"Title {ticket_id}"),
    )


def test_register_agent_persists_roster_row(
    fake_tmux, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_spy_sync(monkeypatch)
    rt = Runtime(_config(), repo_root)
    agent = _RecordingAgent("crow-t1", ticket_id="t1")

    async def _drive() -> None:
        await rt.start()
        assert rt.db is not None
        _insert_ticket(rt.db, "t1")
        rt.register_agent(agent)
        assert rt.get_agent("crow-t1") is agent
        assert rt.get_crow("t1") is agent
        row = rt.db.conn.execute(
            "SELECT agent_id, role, status, ticket_id FROM agents WHERE agent_id = ?",
            ("crow-t1",),
        ).fetchone()
        assert row is not None
        assert row["role"] == "crow"
        assert row["status"] == "running"
        assert row["ticket_id"] == "t1"
        await rt.stop()

    asyncio.run(_drive())


def test_register_agent_leaves_in_memory_index_when_persist_fails(
    fake_tmux, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Current (pre-AgentRuntime) behavior: index insert precedes persist."""
    _install_spy_sync(monkeypatch)
    rt = Runtime(_config(), repo_root)
    agent = _RecordingAgent("crow-t1")

    async def _drive() -> None:
        await rt.start()
        monkeypatch.setattr(
            rt,
            "sync_agent",
            lambda _agent: (_ for _ in ()).throw(RuntimeError("persist boom")),
        )
        with pytest.raises(RuntimeError, match="persist boom"):
            rt.register_agent(agent)
        # Index was updated before persist failed — the rollback gap Phase 2 closes.
        assert rt.get_agent("crow-t1") is agent
        await rt.stop()

    asyncio.run(_drive())


def test_rename_agent_without_persist_diverges_from_roster(
    fake_tmux, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """plan_ops._retarget_plan_runtime calls rename_agent without persist.

    Until a later sync_agent / DB rename, in-memory keys disagree with durable
    agent_id values — the bypass window §2.1 / §3.3 require closing.
    """
    _install_spy_sync(monkeypatch)
    rt = Runtime(_config(), repo_root)
    agent = _RecordingAgent("planner-old", role=AgentRole.PLANNER, ticket_id=None)

    async def _drive() -> None:
        await rt.start()
        rt.register_agent(agent)
        renamed = rt.rename_agent("planner-old", "planner-new")  # persist=None
        assert renamed is agent
        assert agent.id == "planner-new"
        assert rt.get_agent("planner-new") is agent
        assert rt.get_agent("planner-old") is None
        assert rt.db is not None
        # Durable row still keyed by the old id until an explicit persist/DB rename.
        old_row = rt.db.conn.execute(
            "SELECT agent_id FROM agents WHERE agent_id = ?",
            ("planner-old",),
        ).fetchone()
        new_row = rt.db.conn.execute(
            "SELECT agent_id FROM agents WHERE agent_id = ?",
            ("planner-new",),
        ).fetchone()
        assert old_row is not None
        assert new_row is None
        await rt.stop()

    asyncio.run(_drive())


def test_rename_agent_with_persist_updates_roster(
    fake_tmux, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_spy_sync(monkeypatch)
    rt = Runtime(_config(), repo_root)
    agent = _RecordingAgent("a-old", role=AgentRole.COLLABORATOR, ticket_id=None)

    async def _drive() -> None:
        await rt.start()
        rt.register_agent(agent)
        rt.rename_agent("a-old", "a-new", persist=rt.sync_agent)
        assert rt.db is not None
        # sync_agent upserts by the agent's current id; old row remains unless
        # a separate DB rename runs (characterize upsert, not delete-old).
        old_row = rt.db.conn.execute(
            "SELECT agent_id FROM agents WHERE agent_id = ?",
            ("a-old",),
        ).fetchone()
        new_row = rt.db.conn.execute(
            "SELECT agent_id, status FROM agents WHERE agent_id = ?",
            ("a-new",),
        ).fetchone()
        assert old_row is not None
        assert new_row is not None
        assert new_row["status"] == "running"
        await rt.stop()

    asyncio.run(_drive())


def test_rename_agent_leaves_rekeyed_index_when_persist_fails(
    fake_tmux, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Current (pre-AgentRuntime) behavior: rekey precedes persist; no rollback."""
    _install_spy_sync(monkeypatch)
    rt = Runtime(_config(), repo_root)
    agent = _RecordingAgent("a-old", role=AgentRole.COLLABORATOR, ticket_id=None)

    async def _drive() -> None:
        await rt.start()
        rt.register_agent(agent)
        monkeypatch.setattr(
            rt,
            "sync_agent",
            lambda _agent: (_ for _ in ()).throw(RuntimeError("rename persist boom")),
        )
        with pytest.raises(RuntimeError, match="rename persist boom"):
            rt.rename_agent("a-old", "a-new", persist=rt.sync_agent)
        # Indexes already rekeyed; Phase 2 AgentRuntime.rename must roll this back.
        assert agent.id == "a-new"
        assert rt.get_agent("a-new") is agent
        assert rt.get_agent("a-old") is None
        await rt.stop()

    asyncio.run(_drive())


def test_reap_sets_dead_and_preserves_opposite_ticket_role_index(
    fake_tmux, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_spy_sync(monkeypatch)
    rt = Runtime(_config(), repo_root)
    crow = _RecordingAgent("crow-t1", role=AgentRole.CROW, ticket_id="t1")
    handler = _RecordingAgent(
        "crow_handler-t1", role=AgentRole.CROW_HANDLER, ticket_id="t1"
    )

    async def _drive() -> None:
        await rt.start()
        assert rt.db is not None
        _insert_ticket(rt.db, "t1")
        rt.register_agent(crow)
        rt.register_agent(handler)
        await rt.reap("crow-t1")
        assert rt.get_agent("crow-t1") is None
        assert rt.get_crow("t1") is None
        assert rt.get_crow_handler("t1") is handler
        assert handler.stop_calls == []
        # reap calls agent.stop() with no kwargs → failed=True, kill_session=True.
        assert crow.stop_calls == [{"failed": True, "kill_session": True}]
        row = rt.db.conn.execute(
            "SELECT status FROM agents WHERE agent_id = ?",
            ("crow-t1",),
        ).fetchone()
        assert row is not None
        assert row["status"] == "dead"
        await rt.stop()

    asyncio.run(_drive())


# ---------------------------------------------------------------------------
# Sync final reconciliation
# ---------------------------------------------------------------------------


def test_stop_runs_sync_shutdown_final_reconcile(
    fake_tmux, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = _install_spy_sync(monkeypatch)
    rt = Runtime(_config(), repo_root)

    async def _drive() -> list[str]:
        await rt.start()
        boot = list(spy.calls)
        await rt.stop()
        return boot

    boot_calls = asyncio.run(_drive())

    assert boot_calls == ["seed", "spawn_tasks"]
    assert spy.calls == ["seed", "spawn_tasks", "shutdown", "reconcile_all"]


def test_filesystem_sync_shutdown_reconciles_after_cancelling_owned_keys(
    repo_root: Path,
) -> None:
    """Direct supervisor contract used by Runtime.stop."""

    async def _hang() -> None:
        await asyncio.Event().wait()

    class _Doc:
        async def reconcile_all(self) -> None:
            pass

    order: list[str] = []

    async def _drive() -> None:
        doc = _Doc()
        sup = FilesystemSyncSupervisor(
            plan_sync=doc,  # type: ignore[arg-type]
            note_sync=doc,  # type: ignore[arg-type]
            notetaker_context_sync=doc,  # type: ignore[arg-type]
            ticket_sync=doc,  # type: ignore[arg-type]
            report_sync=doc,  # type: ignore[arg-type]
            repo_root=repo_root,
        )

        async def _tracked_reconcile() -> None:
            order.append("reconcile_all")

        sup.reconcile_all = _tracked_reconcile  # type: ignore[method-assign]

        owned = {
            "plan_sync": asyncio.create_task(_hang()),
            "note_sync": asyncio.create_task(_hang()),
            "notetaker_context_sync": asyncio.create_task(_hang()),
            "ticket_sync": asyncio.create_task(_hang()),
            "report_sync": asyncio.create_task(_hang()),
        }
        unrelated = asyncio.create_task(_hang())
        tasks: dict[str, asyncio.Task[None]] = {**owned, "unrelated": unrelated}
        await sup.shutdown(tasks)
        # Owned sync keys removed; unrelated task left for Runtime to cancel.
        assert "unrelated" in tasks
        assert not any(
            k in tasks
            for k in (
                "plan_sync",
                "note_sync",
                "notetaker_context_sync",
                "ticket_sync",
                "report_sync",
            )
        )
        assert all(task.cancelled() for task in owned.values())
        assert order == ["reconcile_all"]
        assert not unrelated.cancelled()
        unrelated.cancel()
        with pytest.raises(asyncio.CancelledError):
            await unrelated

    asyncio.run(_drive())


# ---------------------------------------------------------------------------
# Startup recovery report consumption
# ---------------------------------------------------------------------------


def test_background_tasks_consume_startup_reconcile_report_for_reattach(
    repo_root: Path,
) -> None:
    expected_reattaches = [("t1", "crow-t1"), ("t2", "crow-t2")]
    runtime = SimpleNamespace(
        db=object(),
        startup_reconcile_report=ReconcileReport(
            crows_to_reattach=list(expected_reattaches),
        ),
    )
    orchestrator = SimpleNamespace(reattach_crow=AsyncMock())
    tasks = ServiceBackgroundTasks(
        repo_root=repo_root,
        runtime=runtime,  # type: ignore[arg-type]
        orchestrator=orchestrator,  # type: ignore[arg-type]
    )

    async def _drive() -> None:
        await tasks._reattach_surviving_crows()

    asyncio.run(_drive())

    assert orchestrator.reattach_crow.await_count == len(expected_reattaches)
    for ticket_id, crow_session in expected_reattaches:
        orchestrator.reattach_crow.assert_any_await(ticket_id, crow_session)


def test_background_tasks_skip_reattach_when_report_absent_or_empty(
    repo_root: Path,
) -> None:
    orchestrator = SimpleNamespace(reattach_crow=AsyncMock())

    async def _drive() -> None:
        empty = ServiceBackgroundTasks(
            repo_root=repo_root,
            runtime=SimpleNamespace(db=object(), startup_reconcile_report=None),  # type: ignore[arg-type]
            orchestrator=orchestrator,  # type: ignore[arg-type]
        )
        await empty._reattach_surviving_crows()

        blank = ServiceBackgroundTasks(
            repo_root=repo_root,
            runtime=SimpleNamespace(  # type: ignore[arg-type]
                db=object(),
                startup_reconcile_report=ReconcileReport(),
            ),
            orchestrator=orchestrator,  # type: ignore[arg-type]
        )
        await blank._reattach_surviving_crows()

    asyncio.run(_drive())
    orchestrator.reattach_crow.assert_not_awaited()


def test_runtime_start_stores_startup_reconcile_report(
    fake_tmux, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_spy_sync(monkeypatch)
    expected = ReconcileReport(crows_to_reattach=[("t9", "crow-t9")])
    monkeypatch.setattr(
        "murder.app.service.runtime.reconcile_agents_vs_tmux",
        lambda *_a, **_k: expected,
    )
    rt = Runtime(_config(), repo_root)

    async def _drive() -> ReconcileReport | None:
        await rt.start()
        report = rt.startup_reconcile_report
        await rt.stop()
        return report

    report = asyncio.run(_drive())
    assert report is expected
    assert report.crows_to_reattach == [("t9", "crow-t9")]
