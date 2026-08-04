"""Long-lived async runtime facade (temporary during decomposition).

Process resources (flock/DB/run/log/signal/advanced-log) live on ProcessScope.
Startup recovery is ``startup_recovery.run_startup_recovery``. ServiceHost is
the composition root and owns the top-level AsyncExitStack; this class remains
as a temporary facade for handlers, bootstrap, and characterization tests.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
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
from murder.app.service.process_scope import ProcessScope
from murder.app.service.runtime_lifecycle import kill_project_tmux_sessions
from murder.app.service.startup_recovery import StartupRecoveryResult, run_startup_recovery
from murder.llm.harnesses.versioning import HarnessVersionRegistry
from murder.observability.advanced_log import AdvancedLogBase, NullAdvancedLog
from murder.roster.service import RosterService
from murder.runtime.agent_runtime import AgentRuntime
from murder.runtime.agents.events import AgentEventSink, LoggingAgentEventSink
from murder.runtime.agents.verified_control import VerifiedControlFactory
from murder.runtime.orchestration.notifier import (
    InProcessOrchestrationEventSink,
    OrchestrationHandler,
    SubscriptionHandle,
)
from murder.runtime.orchestration.ports import CommandSubmitter, OrchestrationEventSink
from murder.runtime.orchestration.structured_decisions import StructuredDecisionRouter
from murder.runtime.sessions import SessionService
from murder.runtime.terminal.session_names import SessionNamePolicy
from murder.state.persistence.connection import RepoDb

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
    """Temporary facade over process/session/agent/sync lifecycle owners."""

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
        self._activity_dispatcher_factory = activity_dispatcher_factory
        self._trigger_dispatcher_factory = trigger_dispatcher_factory
        self._process: ProcessScope | None = None
        self._owns_lifecycle = False
        self._stack: AsyncExitStack | None = None
        # Project-wide tmux sweep is for intentional stop only — not start rollback.
        self._lifecycle_committed = False
        # Backing fields for test stubs / facade bind before ProcessScope exists.
        self._db: RepoDb | None = None
        self._run_id: str | None = None
        self._orchestration_events: OrchestrationEventSink | None = None
        self._command_submitter: CommandSubmitter | None = None
        self.sessions: SessionService | None = None
        self.activity_dispatcher: ActivityDispatcher | None = None
        self.trigger_dispatcher: TriggerDispatcher | None = None
        self.agents: AgentRuntime | None = None
        self.event_sink: AgentEventSink = LoggingAgentEventSink()
        self.harness_versions = HarnessVersionRegistry()
        self._sync: FilesystemSyncService | None = None
        self._dispatchers: DispatcherLoops | None = None
        self.plan_sync: PlanSync | None = None
        self.note_sync: NoteSync | None = None
        self.notetaker_context_sync: NotetakerContextSync | None = None
        self.ticket_sync: TicketSync | None = None
        self.report_sync: SimpleDocSync | None = None
        self.documents = DocumentAccess(self.repo_root)
        self.document_editors = DocumentEditorSessions(self.repo_root, self.documents)
        self.startup_reconcile_report: StartupRecoveryResult | None = None
        self.structured_decisions: Any | None = None
        self.roster: RosterService | None = None
        self.session_names = SessionNamePolicy.from_config(config)

    @property
    def db(self) -> RepoDb | None:
        return self._process.db if self._process is not None else self._db

    @db.setter
    def db(self, value: RepoDb | None) -> None:
        self._db = value

    @property
    def run_id(self) -> str | None:
        return self._process.run_id if self._process is not None else self._run_id

    @run_id.setter
    def run_id(self, value: str | None) -> None:
        self._run_id = value

    @property
    def orchestration_events(self) -> OrchestrationEventSink | None:
        if self._process is not None:
            return self._process.events
        return self._orchestration_events

    @orchestration_events.setter
    def orchestration_events(self, value: OrchestrationEventSink | None) -> None:
        self._orchestration_events = value

    @property
    def command_submitter(self) -> CommandSubmitter | None:
        if self._process is not None:
            return self._process.commands
        return self._command_submitter

    @command_submitter.setter
    def command_submitter(self, value: CommandSubmitter | None) -> None:
        self._command_submitter = value

    @property
    def advanced_log(self) -> AdvancedLogBase:
        if self._process is not None:
            return self._process.advanced_log
        return NullAdvancedLog()

    @property
    def session_controllers(self):
        """Compatibility alias for the SessionService-owned registry."""
        return None if self.sessions is None else self.sessions.controllers

    def bind_process(self, process: ProcessScope) -> None:
        """Attach a host-owned ProcessScope (facade does not close it)."""
        self._process = process
        self._owns_lifecycle = False
        self._db = process.db
        self._run_id = process.run_id
        self._orchestration_events = process.events
        self._command_submitter = process.commands

    async def __aenter__(self) -> Runtime:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        await self.stop()

    async def start(self) -> None:  # noqa: PLR0912, PLR0915 - service bootstrap
        """Standalone boot used by characterization tests and ``async with Runtime``."""
        if self._process is not None:
            raise RuntimeError("Runtime already started")
        stack = AsyncExitStack()
        await stack.__aenter__()
        self._owns_lifecycle = True
        try:
            process = await stack.enter_async_context(
                ProcessScope.open(self.config, self.repo_root)
            )
            self._process = process
            stack.push_async_callback(self._sweep_project_tmux_if_authoritative)

            self.roster = RosterService(process.db)
            self.sessions = await stack.enter_async_context(SessionService.open(process.db))
            self.document_editors.bind_session_service(self.sessions)

            verified_factory = VerifiedControlFactory(db=process.db, sessions=self.sessions)
            self.agents = await stack.enter_async_context(
                AgentRuntime.open(
                    db=process.db,
                    roster=self.roster,
                    events=process.events,
                    run_id=process.run_id,
                    advanced_log=process.advanced_log,
                    preserve_tmux_on_close=process.is_external_stop_set,
                    verified_control_factory=verified_factory,
                    lifecycle_events_enabled=(process.recorder_mode != "off"),
                )
            )
            self.agents.command_submitter = process.commands
            self.agents.sessions = self.sessions

            self._sync = FilesystemSyncService.attach(self.repo_root, process.db)
            self.plan_sync = self._sync.plan_sync
            self.note_sync = self._sync.note_sync
            self.notetaker_context_sync = self._sync.notetaker_context_sync
            self.ticket_sync = self._sync.ticket_sync
            self.report_sync = self._sync.report_sync
            self.documents = DocumentAccess(
                self.repo_root,
                process.db,
                plan_sync=self.plan_sync,
                note_sync=self.note_sync,
            )
            self.document_editors.update_documents(self.documents)
            self._sync.seed()

            self.startup_reconcile_report = await run_startup_recovery(
                db=process.db,
                repo_root=self.repo_root,
                agents=self.agents,
                sessions=self.sessions,
            )

            self.structured_decisions = StructuredDecisionRouter(
                db=process.db,
                events=process.events,
                run_id=process.run_id,
                get_agent=self.agents.find,
            )
            self._dispatchers = await stack.enter_async_context(
                DispatcherLoops.open(
                    db=process.db,
                    session_controllers=self.sessions.controllers,
                    activity_factory=self._activity_dispatcher_factory,
                    trigger_factory=self._trigger_dispatcher_factory,
                )
            )
            self.activity_dispatcher = self._dispatchers.activity_dispatcher
            self.trigger_dispatcher = self._dispatchers.trigger_dispatcher
        except BaseException:
            self._lifecycle_committed = False
            self._process = None
            self.sessions = None
            self.agents = None
            self._sync = None
            self._dispatchers = None
            self.roster = None
            self.structured_decisions = None
            self.startup_reconcile_report = None
            self.activity_dispatcher = None
            self.trigger_dispatcher = None
            with contextlib.suppress(Exception):
                await stack.aclose()
            raise
        self._lifecycle_committed = True
        self._stack = stack

    async def _sweep_project_tmux_if_authoritative(self) -> None:
        # Failed-start stack unwind must not project-sweep: recovery may have
        # left surviving Crow panes for post-socket reattach (§7.2).
        if not self._lifecycle_committed:
            return
        process = self._process
        if process is None or process.is_external_stop_set():
            return
        with contextlib.suppress(Exception):
            await kill_project_tmux_sessions(self.session_names)

    async def start_filesystem_sync(self) -> None:
        """Start sync loops after parse-error notifier injection."""
        if self._sync is None:
            raise RuntimeError("filesystem sync is unavailable")
        await self._sync.start()

    async def stop(self) -> None:
        if not self._owns_lifecycle:
            self._clear_local_refs()
            return
        if self._sync is not None:
            with contextlib.suppress(Exception):
                await self._sync.close()
        if self._stack is not None:
            with contextlib.suppress(Exception):
                await self._stack.aclose()
            self._stack = None
        self._clear_local_refs()

    def _clear_local_refs(self) -> None:
        self._process = None
        self._owns_lifecycle = False
        self._lifecycle_committed = False
        self._db = None
        self._run_id = None
        self._orchestration_events = None
        self._command_submitter = None
        self.sessions = None
        self.agents = None
        self._sync = None
        self._dispatchers = None
        self.activity_dispatcher = None
        self.trigger_dispatcher = None
        self.plan_sync = None
        self.note_sync = None
        self.notetaker_context_sync = None
        self.ticket_sync = None
        self.report_sync = None
        self.documents = DocumentAccess(self.repo_root)
        self.document_editors = DocumentEditorSessions(self.repo_root, self.documents)
        self.structured_decisions = None
        self.roster = None
        self.startup_reconcile_report = None

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
        if self._process is None:
            raise RuntimeError("Runtime not started")
        await self._process.wait_for_signal()

    def clear_shutdown_signal(self) -> None:
        """Make an imminent stop authoritative rather than signal-graceful."""
        if self._process is not None:
            self._process.clear_external_stop()

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


__all__ = [
    "ActivityDispatcherFactory",
    "Runtime",
    "TriggerDispatcherFactory",
]
