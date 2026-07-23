"""Long-lived async runtime + supervisor.

Owns the asyncio loop, the SQLite connection, private orchestration signals,
and the lifecycle of all agents. This backend runs headless: application
clients connect to the service-owned WebSocket endpoint. Daemons (e.g. CrowHandler) are coroutines spawned and supervised
here; their "tmux session" is a logfile being tailed for debug
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
import sqlite3
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from murder.app.service.agent_registry import AgentRegistry
from murder.app.service.document_access import DocumentAccess
from murder.app.service.filesystem_sync import FilesystemSyncSupervisor
from murder.app.service.recovery import ReconcileReport, reconcile_agents_vs_tmux
from murder.app.service.runtime_lifecycle import kill_project_tmux_sessions, shutdown_live_agents
from murder.app.service.terminal_capture import (
    CapturedTerminalFrame,
    capture_persisted_tmux_frame,
)
from murder.llm.harnesses.versioning import HarnessVersionRegistry
from murder.observability.advanced_log import (
    AdvancedLogBase,
    ArtifactRefRecord,
    NullAdvancedLog,
    StateMutationRecord,
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
from murder.runtime.agents.events import AgentEventSink, LoggingAgentEventSink
from murder.runtime.orchestration.command_repository import (
    PersistingCommandSubmitter,
    SqliteCommandRepository,
)
from murder.runtime.orchestration.events import AgentLifecycleEvent, OrchestrationEvent
from murder.runtime.orchestration.notifier import (
    InProcessOrchestrationEventSink,
    OrchestrationHandler,
    SubscriptionHandle,
)
from murder.runtime.orchestration.ports import CommandSubmitter, OrchestrationEventSink
from murder.runtime.orchestration.structured_decisions import StructuredDecisionRouter
from murder.runtime.sessions.registry import (
    close_registry_for_connection,
    registry_for_connection,
)
from murder.runtime.terminal import tmux
from murder.state.persistence.activities import (
    reap_expired_claims,
    reap_expired_reservations,
)
from murder.state.persistence.conversation import mark_stale_conversations
from murder.state.persistence.runs import end_run as _db_end_run
from murder.state.persistence.runs import insert_run as _db_insert_run
from murder.state.persistence.runs import (
    set_run_advanced_log_path as _db_set_run_advanced_log_path,
)
from murder.state.persistence.schema import get_db as _db_connect
from murder.state.persistence.schema import init_db as _db_init_schema
from murder.state.storage.filesystem import acquire_flock, release_flock
from murder.state.storage.paths import (
    db_path,
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
    from murder.runtime.agents.base import LifecycleParticipant
    from murder.runtime.trigger_dispatcher import TriggerDispatcher
    from murder.user_config import UserConfig
    from murder.work.notes.sync import NoteSync, NotetakerContextSync
    from murder.work.plans.sync import PlanSync
    from murder.work.simple_doc_sync import SimpleDocSync
    from murder.work.tickets.sync import TicketSync

ActivityDispatcherFactory = Callable[[sqlite3.Connection], "ActivityDispatcher"]
TriggerDispatcherFactory = Callable[[sqlite3.Connection], "TriggerDispatcher"]


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
        self.db: sqlite3.Connection | None = None
        self.session_controllers: Any | None = None
        self._activity_dispatcher_factory = activity_dispatcher_factory
        self._trigger_dispatcher_factory = trigger_dispatcher_factory
        self.activity_dispatcher: ActivityDispatcher | None = None
        self.trigger_dispatcher: TriggerDispatcher | None = None
        self.orchestration_events: OrchestrationEventSink | None = None
        self.command_submitter: CommandSubmitter | None = None
        self.run_id: str | None = None
        self._agents = AgentRegistry()
        self.agents = self._agents
        self.event_sink: AgentEventSink = LoggingAgentEventSink()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        # Holds in-flight private orchestration tasks so shutdown can drain
        # them deterministically.
        self._emit_tasks: set[asyncio.Task[None]] = set()
        self._shutdown = asyncio.Event()
        self.harness_versions = HarnessVersionRegistry()
        self._external_stop = asyncio.Event()
        self._lock_fd: int | None = None
        self._sync: FilesystemSyncSupervisor | None = None
        self.plan_sync: PlanSync | None = None
        self.note_sync: NoteSync | None = None
        self.notetaker_context_sync: NotetakerContextSync | None = None
        self.ticket_sync: TicketSync | None = None
        self.report_sync: SimpleDocSync | None = None
        self.documents = DocumentAccess(self.repo_root)
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
        self.crow_ask_router: Callable[[str | None, str, str], Awaitable[None]] | None = None
        self.roster = RosterService(db_path(self.repo_root))

    async def __aenter__(self) -> Runtime:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        await self.stop()

    async def _run_tick_loop(
        self,
        name: str,
        tick: Callable[[], Awaitable[Any]],
    ) -> None:
        while not self._shutdown.is_set():
            try:
                await tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.getLogger(__name__).exception("%s dispatcher tick failed; retrying", name)
            await asyncio.sleep(1)

    async def _phase4_activity_loop(self) -> None:
        assert self.activity_dispatcher is not None
        await self._run_tick_loop("activity", self.activity_dispatcher.tick)

    async def _phase4_trigger_loop(self) -> None:
        assert self.trigger_dispatcher is not None

        async def tick() -> None:
            assert self.trigger_dispatcher is not None
            self.trigger_dispatcher.tick()

        await self._run_tick_loop("trigger", tick)

    async def start(self) -> None:  # noqa: PLR0912, PLR0915 - service bootstrap
        self._shutdown.clear()
        self._external_stop.clear()
        self._lock_fd = acquire_flock(lock_path(self.repo_root))
        # Everything after the flock is fallible (tmux/subprocess, filesystem,
        # DB). A throw here must not leave the repo flock held and the sqlite
        # connection open -- ``stop()`` never runs because ``__aexit__`` only
        # fires after ``__aenter__`` returns. Release the lock + close the DB
        # on any failure before re-raising.
        try:
            self.db = _db_connect(db_path(self.repo_root))
            _db_init_schema(self.db)
            self.session_controllers = registry_for_connection(self.db)
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
            # mode is off; otherwise creates a per-session DB under .murder/advlogs/,
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
            # known large per-run artifacts. Stat is existence-guarded; the
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
            self.structured_decisions = StructuredDecisionRouter(self)
            # The flight recorder is a normal bus SUBSCRIBER (plan §2.5.A): when
            # on, it captures EVERY event (filter=None) and routes each to its
            # record_family table. Registered before any sync task spawns so no
            # early event is missed. Below the `advanced` rung it does not exist
            # — no subscription, no DB, no per-run disk cost.
            if mode != "off":
                self._recorder_sub = orchestration_events.subscribe(
                    self._record_orchestration_event
                )
                self._agents.on_lifecycle = self._emit_agent_lifecycle
            self._sync = FilesystemSyncSupervisor.attach(self.repo_root, self.db)
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
            # Seeding stays on the boot path (cheap, idempotent — restores missing
            # examples before the loops scan). The heavy markdown->DB reconcile is now
            # carried by the spawned per-category loops below: non-blocking, single-pass,
            # parallel — so it no longer blocks socket readiness nor runs twice at boot.
            self._sync.seed()
            self._tasks.update(self._sync.spawn_tasks())
            # External work is the final subsystem enabled at boot. Session/tmux
            # reconciliation and all Runtime core state must exist before either
            # dispatcher may recover leases or execute an activity.
            reap_expired_claims(self.db)
            reap_expired_reservations(self.db)
            if self._activity_dispatcher_factory is not None:
                self.activity_dispatcher = self._activity_dispatcher_factory(self.db)
                self._tasks["phase4-activities"] = asyncio.create_task(self._phase4_activity_loop())
            if self._trigger_dispatcher_factory is not None:
                self.trigger_dispatcher = self._trigger_dispatcher_factory(self.db)
                self._tasks["phase4-triggers"] = asyncio.create_task(self._phase4_trigger_loop())
        except BaseException:
            for task in self._tasks.values():
                task.cancel()
            if self._tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await asyncio.gather(*self._tasks.values(), return_exceptions=True)
            self._tasks.clear()
            if self.db is not None:
                with contextlib.suppress(Exception):
                    await close_registry_for_connection(self.db)
            with contextlib.suppress(Exception):
                if self.db is not None:
                    self.db.close()
            self.db = None
            self.session_controllers = None
            self.orchestration_events = None
            self.command_submitter = None
            self.run_id = None
            self._sync = None
            self.structured_decisions = None
            if self._lock_fd is not None:
                with contextlib.suppress(Exception):
                    release_flock(self._lock_fd)
                self._lock_fd = None
                with contextlib.suppress(FileNotFoundError, OSError):
                    lock_path(self.repo_root).unlink()
            raise

    async def stop(self) -> None:
        self._shutdown.set()
        if self._emit_tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.gather(*list(self._emit_tasks), return_exceptions=True)
            self._emit_tasks.clear()
        if self._sync is not None:
            await self._sync.shutdown(self._tasks)
        for t in list(self._tasks.values()):
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._tasks.clear()
        graceful = self._external_stop.is_set()
        await shutdown_live_agents(self._agents, graceful=graceful)
        with contextlib.suppress(Exception):
            await kill_project_tmux_sessions(self)
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
            with contextlib.suppress(Exception):
                await close_registry_for_connection(self.db)
            self.session_controllers = None
            self.db.close()
            self.db = None
        self._sync = None
        self.plan_sync = None
        self.note_sync = None
        self.notetaker_context_sync = None
        self.ticket_sync = None
        self.report_sync = None
        self.documents = DocumentAccess(self.repo_root)
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

    def _emit_agent_lifecycle(
        self,
        *,
        op: str,
        agent_id: str,
        details: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> None:
        """Schedule an ``AgentLifecycleEvent`` publish from a SYNC registry hook.

        Wired onto ``AgentRegistry.on_lifecycle`` at start so register / rename /
        clear ride the one bus aspect into ``agent_records`` (force-stop reaches
        this directly from agent_ops). AgentLifecycleEvent is purely forensic, so
        gate on the recorder being on: below the ``advanced`` rung there is no
        subscriber and the registry hook is never wired, so the only path that
        could fire here is force-stop — which must also be a no-op when off.
        Otherwise best-effort by contract: a no-op before the bus exists and
        DURING shutdown — ``clear`` fires from the teardown path, and the plan
        says emit-before-teardown or treat as best-effort rather than add a
        hot-path duplicate write to dodge the race.
        """
        if (
            self._recorder_sub is None
            or self.orchestration_events is None
            or self.run_id is None
            or self._shutdown.is_set()
        ):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(
            self.orchestration_events.publish(
                AgentLifecycleEvent(
                    run_id=self.run_id,
                    agent_id=agent_id,
                    op=op,  # type: ignore[arg-type]
                    details=details or {},
                    reason=reason,
                )
            )
        )
        self._emit_tasks.add(task)
        task.add_done_callback(self._emit_tasks.discard)

    def sync_agent(self, agent: LifecycleParticipant) -> None:
        """Persist an agent plus its roster invalidation as one feature operation."""
        if self.db is None:
            return
        worktree_path = getattr(agent, "worktree_path", None)
        self.roster.sync_agent(
            self.db,
            agent_id=agent.id,
            role=agent.role.value,
            ticket_id=agent.ticket_id,
            session=agent.session,
            harness=getattr(getattr(agent, "harness", None), "kind", None),
            model=getattr(agent, "startup_model", None),
            status=agent.status.value,
            start_commit=getattr(agent, "start_commit", None),
            worktree_path=str(worktree_path) if worktree_path is not None else None,
            pid=None,
        )
        # Flight-recorder data is observational only; roster refresh travels
        # through the transactional feature projection input above.
        self.advanced_log.record_state_mutation(
            StateMutationRecord(
                entity="agent",
                agent_id=agent.id,
                role=agent.role.value,
                ticket_id=agent.ticket_id,
                session=agent.session,
                status=agent.status.value,
                harness=getattr(getattr(agent, "harness", None), "kind", None),
                model=getattr(agent, "startup_model", None),
                worktree_path=str(worktree_path) if worktree_path is not None else None,
            )
        )

    def register_agent(self, agent: LifecycleParticipant) -> None:
        self._agents.register(agent, persist=self.sync_agent)

    def get_agent(self, agent_id: str) -> LifecycleParticipant | None:
        return self._agents.get_agent(agent_id)

    def get_crow(self, ticket_id: str) -> LifecycleParticipant | None:
        return self._agents.get_crow(ticket_id)

    def get_crow_handler(self, ticket_id: str) -> LifecycleParticipant | None:
        return self._agents.get_crow_handler(ticket_id)

    async def reap(self, agent_id: str) -> None:
        await self._agents.reap(
            agent_id,
            tasks=self._tasks,
            db=self.db,
            set_dead=lambda conn, agent_id, status: self.roster.set_agent_status(
                conn, agent_id, status
            ),
        )

    def rename_agent(
        self,
        old_agent_id: str,
        new_agent_id: str,
        *,
        persist: Callable[[LifecycleParticipant], None] | None = None,
    ) -> LifecycleParticipant | None:
        return self._agents.rename_agent(old_agent_id, new_agent_id, persist=persist)

    async def supervise(self, agent_id: str) -> None:
        """Restart policy placeholder — daemons own their poll loops."""
        return None

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

    async def capture_terminal_frame(self, session_id: UUID) -> CapturedTerminalFrame:
        """Capture a terminal strictly through a persisted session UUID."""
        if self.db is None:
            raise RuntimeError("service database is unavailable")
        return await capture_persisted_tmux_frame(self.db, session_id)

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
