"""Long-lived async runtime + supervisor.

Owns the asyncio loop, the SQLite connection, private orchestration signals,
and the lifecycle of all agents. This backend runs headless: application
clients connect to the service-owned WebSocket endpoint. Daemons (e.g. CrowHandler) are coroutines spawned and supervised
here. Their "tmux session" is a logfile being tailed for debug
visibility, not a real interactive session.

Process model rules:
- One murder process per repo. flock on `.murder/.lock` enforces.
- Graceful shutdown drains the bus, signals Crows, kills tmux sessions.
- Crash recovery: on startup, reconcile DB ↔ tmux ↔ filesystem before
  resuming.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import signal
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from murder.app.service.dispatcher_loops import (
    ActivityDispatcherFactory,
    DispatcherLoops,
    TriggerDispatcherFactory,
)
from murder.app.service.document_access import DocumentAccess
from murder.app.service.document_editor_sessions import DocumentEditorSessions, EditorSession
from murder.app.service.filesystem_sync import FilesystemSyncService
from murder.app.service.recovery import ReconcileReport, reconcile_agents_vs_tmux
from murder.app.service.runtime_lifecycle import kill_project_tmux_sessions
from murder.llm.harnesses.versioning import HarnessVersionRegistry
from murder.observability.advanced_log import (
    AdvancedLogBase,
    ArtifactRefRecord,
    NullAdvancedLog,
    open_advanced_log,
    set_current_advanced_log,
)
from murder.observability.log_context import set_run_id
from murder.observability.logging_setup import (
    configure_logging,
    resolve_log_level,
    resolve_recorder_mode,
)
from murder.roster.service import RosterService
from murder.runtime.agent_runtime import AgentRuntime
from murder.runtime.agents.events import AgentEventSink, LoggingAgentEventSink
from murder.runtime.agents.verified_control import VerifiedControlFactory
from murder.runtime.orchestration.command_repository import (
    PersistingCommandSubmitter,
    SqliteCommandRepository,
)
from murder.runtime.orchestration.events import OrchestrationEvent
from murder.runtime.orchestration.notifier import (
    InProcessOrchestrationEventSink,
    OrchestrationHandler,
    SubscriptionHandle,
)
from murder.runtime.orchestration.ports import CommandSubmitter, OrchestrationEventSink
from murder.runtime.orchestration.structured_decisions import StructuredDecisionRouter
from murder.runtime.sessions import SessionService
from murder.runtime.terminal import tmux
from murder.runtime.terminal.session_names import SessionNamePolicy
from murder.state.persistence.activities import (
    reap_expired_claims,
    reap_expired_reservations,
)
from murder.state.persistence.connection import RepoDb, open_repo_db
from murder.state.persistence.conversation import mark_stale_conversations
from murder.state.persistence.runs import end_run as _db_end_run
from murder.state.persistence.runs import insert_run as _db_insert_run
from murder.state.persistence.runs import (
    set_run_advanced_log_path as _db_set_run_advanced_log_path,
)
from murder.state.storage.filesystem import acquire_flock, release_flock
from murder.state.storage.paths import (
    lock_path,
    logs_dir,
    panes_dir,
    service_log,
)
from murder.state.storage.run_id_allocation import allocate_run_id
from murder.work.workflows.service import WorkflowRuntime

if TYPE_CHECKING:
    from murder.config import Config
    from murder.runtime.activity_dispatcher import ActivityDispatcher
    from murder.runtime.trigger_dispatcher import TriggerDispatcher
    from murder.user_config import UserConfig
    from murder.work.notes.sync import NoteSync, NotetakerContextSync
    from murder.work.plans.sync import PlanSync
    from murder.work.simple_doc_sync import SimpleDocSync
    from murder.work.tickets.sync import TicketSync


class Runtime:
    """Async context manager owning the murder process lifecycle."""

    def __init__(
        self,
        config: Config,
        repo_root: Path,
        user_cfg: UserConfig | None = None,
        *,
        activity_dispatcher_factory: ActivityDispatcherFactory | None = None,
        trigger_dispatcher_factory: TriggerDispatcherFactory | None = None,
    ) -> None:
        self.config = config
        self.repo_root = repo_root
        self.user_cfg = user_cfg
        self.db: RepoDb | None = None
        self.sessions: SessionService | None = None
        self._activity_dispatcher_factory = activity_dispatcher_factory
        self._trigger_dispatcher_factory = trigger_dispatcher_factory
        self.activity_dispatcher: ActivityDispatcher | None = None
        self.trigger_dispatcher: TriggerDispatcher | None = None
        self.orchestration_events: OrchestrationEventSink | None = None
        self.command_submitter: CommandSubmitter | None = None
        self.run_id: str | None = None
        self.agents: AgentRuntime | None = None
        self.event_sink: AgentEventSink = LoggingAgentEventSink()
        self.harness_versions = HarnessVersionRegistry()
        self._external_stop = asyncio.Event()
        self._lock_fd: int | None = None
        self._sync: FilesystemSyncService | None = None
        self._dispatchers: DispatcherLoops | None = None
        self.plan_sync: PlanSync | None = None
        self.note_sync: NoteSync | None = None
        self.notetaker_context_sync: NotetakerContextSync | None = None
        self.ticket_sync: TicketSync | None = None
        self.report_sync: SimpleDocSync | None = None
        self.documents = DocumentAccess(self.repo_root)
        self.document_editors = DocumentEditorSessions(self.repo_root, self.documents)
        self.startup_reconcile_report: ReconcileReport | None = None
        # Phase 2 flight recorder. Always present (no-op when off) so Wave 4
        # boundaries can call ``self.advanced_log.record_*`` unconditionally.
        self.advanced_log: AdvancedLogBase = NullAdvancedLog()
        set_current_advanced_log(self.advanced_log)
        # The recorder's bus subscription (only when advanced logging is on).
        self._recorder_sub: SubscriptionHandle | None = None
        # Durable observation→external-decision→verified-execution router.
        # It owns no policy and is initialized only once the persisted bus exists.
        self.structured_decisions: Any | None = None
        self.roster: RosterService | None = None
        self.session_names = SessionNamePolicy.from_config(config)

    @property
    def session_controllers(self) -> SessionControllerRegistry | None:
        """Compatibility alias for the SessionService-owned registry."""

        return None if self.sessions is None else self.sessions.controllers

    async def __aenter__(self) -> Runtime:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        await self.stop()

    async def start(self) -> None:  # noqa: PLR0912, PLR0915 - service bootstrap
        self._external_stop.clear()
        self._lock_fd = acquire_flock(lock_path(self.repo_root))
        # Everything after the flock is fallible (tmux/subprocess, filesystem,
        # DB). A throw here must not leave the repo flock held and the sqlite
        # connection open -- ``stop()`` never runs because ``__aexit__`` only
        # fires after ``__aenter__`` returns. Release the lock + close the DB
        # on any failure before re-raising.
        try:
            self.db = open_repo_db(self.repo_root)
            self.roster = RosterService(self.db)
            self.sessions = SessionService(self.db)
            self.document_editors.bind_session_service(self.sessions)
            WorkflowRuntime(self.db).recover_pending_signals()
            live_sessions = set(await tmux.list_sessions())
            report = reconcile_agents_vs_tmux(self.db, live_sessions)
            self.startup_reconcile_report = report
            if report:
                logging.getLogger(__name__).info("startup reconcile: %s", report.summary())
            for session in report.sessions_to_kill:
                with contextlib.suppress(Exception):
                    await tmux.kill_session(session)
            stale_count = mark_stale_conversations(self.db)
            if stale_count:
                logging.getLogger(__name__).info(
                    "startup: marked %d in_progress conversation(s) stale", stale_count
                )
            self.run_id = allocate_run_id(self.repo_root)
            # Pin the run id into the ambient log context and attach the per-run
            # structured file handler now that the run dir tree exists.
            set_run_id(self.run_id)
            configure_logging(
                level=resolve_log_level(),
                log_path=service_log(self.repo_root, self.run_id),
            )
            snap = json.dumps(self.config.model_dump(mode="json"), default=str)
            _db_insert_run(self.db, self.run_id, snap)
            # Phase 2: open the opt-in flight recorder. No-op when the recorder
            # mode is off. Otherwise it creates a per-session DB under .murder/advlogs/,
            # writes the session_info row (with the main-DB schema marker), and
            # stores the pointer on the runs row.
            mode = resolve_recorder_mode()
            self.advanced_log = open_advanced_log(self.repo_root, self.run_id, mode)
            set_current_advanced_log(self.advanced_log)
            await self.advanced_log.start()
            self.advanced_log.write_session_info(main_db=self.db)
            if mode != "off":
                with contextlib.suppress(Exception):
                    _db_set_run_advanced_log_path(
                        self.db, self.run_id, str(getattr(self.advanced_log, "_db_path", ""))
                    )
            # Phase 2 (Step 2.6): register REFERENCES (never contents) to the
            # known large per-run artifacts. Stat is existence-guarded. The
            # panes dir is referenced as a whole (per-pane logs are created
            # lazily later). No-op when advanced logging is off.
            for artifact in (
                service_log(self.repo_root, self.run_id),
                logs_dir(self.repo_root) / "supervisor.ndjson",
                panes_dir(self.repo_root, self.run_id),
            ):
                size: int | None = None
                with contextlib.suppress(OSError):
                    if artifact.exists():
                        size = artifact.stat().st_size
                self.advanced_log.record_artifact_ref(
                    ArtifactRefRecord(
                        path=str(artifact),
                        size=size,
                        sha=None,
                        links={"run_id": self.run_id},
                    )
                )
            orchestration_events = InProcessOrchestrationEventSink()
            self.orchestration_events = orchestration_events
            self.command_submitter = PersistingCommandSubmitter(
                SqliteCommandRepository(self.db), orchestration_events
            )
            # The flight recorder is a normal bus SUBSCRIBER (plan §2.5.A): when
            # on, it captures EVERY event (filter=None) and routes each to its
            # record_family table. Registered before any sync task spawns so no
            # early event is missed. Below the `advanced` rung it does not exist
            # — no subscription, no DB, no per-run disk cost.
            if mode != "off":
                self._recorder_sub = orchestration_events.subscribe(
                    self._record_orchestration_event
                )
            assert self.db is not None and self.run_id is not None and self.roster is not None
            verified_factory = VerifiedControlFactory(db=self.db, sessions=self.sessions)
            self.agents = AgentRuntime(
                db=self.db,
                roster=self.roster,
                events=orchestration_events,
                run_id=self.run_id,
                advanced_log=self.advanced_log,
                preserve_tmux_on_close=lambda: self._external_stop.is_set(),
                verified_control_factory=verified_factory,
                lifecycle_events_enabled=(mode != "off"),
            )
            self.agents.command_submitter = self.command_submitter
            self.agents.sessions = self.sessions
            self.structured_decisions = StructuredDecisionRouter(
                db=self.db,
                events=orchestration_events,
                run_id=self.run_id,
                get_agent=self.agents.find,
            )
            self._sync = FilesystemSyncService.attach(self.repo_root, self.db)
            self.plan_sync = self._sync.plan_sync
            self.note_sync = self._sync.note_sync
            self.notetaker_context_sync = self._sync.notetaker_context_sync
            self.ticket_sync = self._sync.ticket_sync
            self.report_sync = self._sync.report_sync
            self.documents = DocumentAccess(
                self.repo_root,
                self.db,
                plan_sync=self.plan_sync,
                note_sync=self.note_sync,
            )
            self.document_editors.update_documents(self.documents)
            # Seeding stays on the boot path (cheap, idempotent — restores missing
            # examples before the loops scan). Sync loops start later, after the
            # host injects the parse-error notifier (Phase 3 ordering).
            self._sync.seed()
            # External work is the final subsystem enabled at boot. Session/tmux
            # reconciliation and all Runtime core state must exist before either
            # dispatcher may recover leases or execute an activity.
            reap_expired_claims(self.db)
            reap_expired_reservations(self.db)
            self._dispatchers = DispatcherLoops()
            await self._dispatchers.start(
                db=self.db,
                session_controllers=self.sessions.controllers,
                activity_factory=self._activity_dispatcher_factory,
                trigger_factory=self._trigger_dispatcher_factory,
            )
            self.activity_dispatcher = self._dispatchers.activity_dispatcher
            self.trigger_dispatcher = self._dispatchers.trigger_dispatcher
        except BaseException:
            if self._dispatchers is not None:
                with contextlib.suppress(Exception):
                    await self._dispatchers.close()
                self._dispatchers = None
            if self._sync is not None:
                with contextlib.suppress(Exception):
                    await self._sync.close()
                self._sync = None
            if self.sessions is not None:
                with contextlib.suppress(Exception):
                    await self.sessions.close()
                self.sessions = None
            with contextlib.suppress(Exception):
                if self.db is not None:
                    self.db.close()
            self.db = None
            self.orchestration_events = None
            self.command_submitter = None
            self.run_id = None
            self.structured_decisions = None
            self.agents = None
            self.activity_dispatcher = None
            self.trigger_dispatcher = None
            if self._lock_fd is not None:
                with contextlib.suppress(Exception):
                    release_flock(self._lock_fd)
                self._lock_fd = None
                with contextlib.suppress(FileNotFoundError, OSError):
                    lock_path(self.repo_root).unlink()
            raise

    async def start_filesystem_sync(self) -> None:
        """Start sync loops after parse-error notifier injection."""
        if self._sync is None:
            raise RuntimeError("filesystem sync is unavailable")
        await self._sync.start()

    async def stop(self) -> None:
        if self._sync is not None:
            await self._sync.close()
        if self._dispatchers is not None:
            await self._dispatchers.close()
            self._dispatchers = None
        self.activity_dispatcher = None
        self.trigger_dispatcher = None
        graceful = self._external_stop.is_set()
        # Stop agents before closing sessions / sweeping tmux (§7.2).
        if self.agents is not None:
            await self.agents.close()
            self.agents = None
        if self.sessions is not None:
            await self.sessions.close()
            self.sessions = None
        # Sweep only on authoritative stop (graceful preserves tmux end-to-end).
        if not graceful:
            with contextlib.suppress(Exception):
                await kill_project_tmux_sessions(self.session_names)
        # Stop feeding the recorder, then drain + close it before the main DB.
        if self._recorder_sub is not None:
            self._recorder_sub.cancel()
            self._recorder_sub = None
        with contextlib.suppress(Exception):
            await self.advanced_log.stop()
        self.advanced_log = NullAdvancedLog()
        set_current_advanced_log(self.advanced_log)
        if self.run_id and self.db is not None:
            _db_end_run(self.db, self.run_id)
        if self.db is not None:
            self.db.close()
            self.db = None
        self._sync = None
        self.plan_sync = None
        self.note_sync = None
        self.notetaker_context_sync = None
        self.ticket_sync = None
        self.report_sync = None
        self.documents = DocumentAccess(self.repo_root)
        self.document_editors = DocumentEditorSessions(self.repo_root, self.documents)
        self.orchestration_events = None
        self.command_submitter = None
        self.structured_decisions = None
        self.run_id = None
        if self._lock_fd is not None:
            release_flock(self._lock_fd)
            self._lock_fd = None
            with contextlib.suppress(FileNotFoundError, OSError):
                lock_path(self.repo_root).unlink()

    async def _record_orchestration_event(self, event: OrchestrationEvent) -> None:
        """Orchestration-event subscriber handler for the flight recorder.

        Enqueue-and-return: the writer copies the correlation ids off the ambient
        ``log_context`` (which ``asyncio.gather`` propagated from the publisher),
        then returns immediately. Do NOT spawn a detached task here — that would
        run outside the publish context and sever the ids.
        """
        self.advanced_log.record_orchestration_event(event)

    @property
    def crow_ask_router(self):
        """Compatibility: crow ask routing lives on AgentRuntime after Phase 2."""
        if self.agents is None:
            return None
        return self.agents.crow_ask_router

    @crow_ask_router.setter
    def crow_ask_router(self, value) -> None:
        if self.agents is not None:
            self.agents.crow_ask_router = value

    @asynccontextmanager
    async def subscription(
        self,
        handler: OrchestrationHandler,
    ) -> AsyncGenerator[SubscriptionHandle, None]:
        events = self.orchestration_events
        if not isinstance(events, InProcessOrchestrationEventSink):
            raise RuntimeError("Runtime not started (no orchestration event fanout)")
        handle = events.subscribe(handler)
        try:
            yield handle
        finally:
            handle.cancel()

    async def run_until_signal(self) -> None:
        """Block until SIGINT/SIGTERM (Linux/macOS). Used after CLI kickoff."""
        loop = asyncio.get_running_loop()

        def _wake() -> None:
            self._external_stop.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, _wake)
        await self._external_stop.wait()

    def clear_shutdown_signal(self) -> None:
        """Make an imminent stop authoritative rather than signal-graceful.

        The service process uses this when its own lifecycle is ending.  It is
        intentionally a public lifecycle hook instead of leaking the event
        used internally by ``run_until_signal`` to composition code.
        """
        self._external_stop.clear()

    def configure_parse_error_notifier(
        self,
        send_message: Callable[[str, str], Awaitable[None]],
    ) -> None:
        """Attach the application delivery hook after its orchestrator exists."""
        if self._sync is None:
            raise RuntimeError("filesystem sync is unavailable")
        self._sync.set_parse_error_notifier(send_message)

    async def start_document_editor(
        self, kind: str, name: str, columns: int, rows: int
    ) -> tuple[EditorSession, bool]:
        return await self.document_editors.start(kind, name, columns=columns, rows=rows)

    async def resize_document_editor(self, session_id: UUID, columns: int, rows: int) -> None:
        await self.document_editors.resize(session_id, columns=columns, rows=rows)

    async def document_editor_status(self, session_id: UUID) -> tuple[EditorSession, bool]:
        session = self.document_editors.get(session_id)
        return session, await self.document_editors.active(session_id)

    async def reconcile_plan(self, name: str) -> None:
        await self.documents.reconcile_plan(name)

    async def open_plan_in_editor(self, name: str, preferred_editor: str | None = None) -> int:
        return await self.documents.open_plan_in_editor(name, preferred_editor)

    async def open_note_in_editor(self, name: str, preferred_editor: str | None = None) -> int:
        return await self.documents.open_note_in_editor(name, preferred_editor)

    async def open_report_in_editor(self, name: str, preferred_editor: str | None = None) -> int:
        return await self.documents.open_report_in_editor(name, preferred_editor)

    def open_editor_blocking(self, path: Path, preferred_editor: str | None = None) -> int:
        return self.documents.open_editor_blocking(path, preferred_editor)

    def plan_path_for(self, name: str) -> Path:
        return self.documents.plan_path_for(name)

    def note_path_for(self, name: str) -> Path:
        return self.documents.note_path_for(name)

    def report_path_for(self, name: str) -> Path:
        return self.documents.report_path_for(name)
