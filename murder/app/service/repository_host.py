"""RepositoryHost — per-repository composition root for the murder service.

Daemon-level concerns (socket listener, session registry, signal wait, user
themes/config loading) live outside this class. Callers pass ``user_config``
in and consume the exposed dispatcher / fact / projection / terminal inputs
to attach a socket session.
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from contextvars import Context
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

from murder.app.protocol.requests import CommandName, QueryName
from murder.app.service.application import ApplicationDispatcher, ApplicationHandler
from murder.app.service.background_tasks import ServiceBackgroundTasks
from murder.app.service.bootstrap import start_supervisor_workers
from murder.app.service.dispatcher_loops import (
    ActivityDispatcherFactory,
    DispatcherLoops,
    TriggerDispatcherFactory,
)
from murder.app.service.document_editors import DocumentEditorService
from murder.app.service.documents import DocumentService
from murder.app.service.filesystem_sync import FilesystemSyncService
from murder.app.service.process_scope import ProcessScope
from murder.app.service.projection_registry import ProjectionProviderRegistry
from murder.app.service.read_model import ServiceReadModel
from murder.app.service.runtime_lifecycle import kill_project_tmux_sessions
from murder.app.service.socket_server import (
    TerminalCapture,
    TerminalInput,
    TerminalInputValidator,
    TerminalOutputOpen,
)
from murder.app.service.startup_recovery import StartupRecoveryResult, run_startup_recovery
from murder.app.service.supervisor import Supervisor
from murder.config import Config
from murder.facts.log import FactLog, ProjectionInputLog
from murder.llm.harnesses.versioning import HarnessVersionRegistry
from murder.observability.log_context import create_task_with_context
from murder.roster.service import RosterService
from murder.runtime.agent_runtime import AgentRuntime
from murder.runtime.agents.events import AgentEventSink, LoggingAgentEventSink
from murder.runtime.agents.verified_control import VerifiedControlFactory
from murder.runtime.orchestration.orchestrator import Orchestrator
from murder.runtime.orchestration.structured_decisions import StructuredDecisionRouter
from murder.runtime.sessions import (
    PrincipalKind,
    PrincipalRef,
    SessionService,
    SessionTransport,
    WriterMode,
    WriteTerminalInput,
)
from murder.runtime.terminal.session_names import SessionNamePolicy
from murder.user_config import UserConfig

PlanSeedFailureNotifier = Callable[[str | None, str, str], Awaitable[None]]


@dataclass
class _RunningService:
    """Private host typestate. Never injected into child components."""

    process: ProcessScope
    sessions: SessionService
    agents: AgentRuntime
    sync: FilesystemSyncService
    documents: DocumentService
    editors: DocumentEditorService
    dispatchers: DispatcherLoops
    orchestrator: Orchestrator
    recovery: StartupRecoveryResult
    harness_versions: HarnessVersionRegistry
    event_sink: AgentEventSink
    structured_decisions: StructuredDecisionRouter
    session_names: SessionNamePolicy
    roster: RosterService


@dataclass
class RepositoryHost:
    """Wires runtime and application services for one repository.

    Responsibility (keep it this narrow): the per-repo COMPOSITION ROOT and
    lifecycle owner. ``start``/``stop`` wire the collaborators and own the
    background tasks. ``register_application_handlers`` just delegates to the
    ``handlers/`` package. This class deliberately holds NO request logic and
    NO process-wide socket / signal / user-config loading.
    """

    config: Config
    repo_root: Path
    user_config: UserConfig | None = None
    # Shared daemon registry when running under DaemonHost; else a private one.
    harness_versions: HarnessVersionRegistry | None = None
    activity_dispatcher_factory: ActivityDispatcherFactory | None = None
    trigger_dispatcher_factory: TriggerDispatcherFactory | None = None
    read_model: ServiceReadModel | None = None
    fact_log: FactLog | None = None
    projection_input_log: ProjectionInputLog | None = None
    projection_providers: ProjectionProviderRegistry = field(
        default_factory=ProjectionProviderRegistry, repr=False
    )
    application_dispatcher: ApplicationDispatcher | None = None
    terminal_capture: TerminalCapture | None = field(default=None, repr=False)
    terminal_output_open: TerminalOutputOpen | None = field(default=None, repr=False)
    terminal_input: TerminalInput | None = field(default=None, repr=False)
    terminal_input_validator: TerminalInputValidator | None = field(
        default=None, repr=False
    )
    orchestrator: Orchestrator | None = None
    supervisor: Supervisor | None = None
    background_tasks: ServiceBackgroundTasks | None = field(default=None, repr=False)
    _application_queries: dict[QueryName, ApplicationHandler] = field(
        default_factory=dict, repr=False
    )
    _application_commands: dict[CommandName, ApplicationHandler] = field(
        default_factory=dict, repr=False
    )
    _plan_seed_failure_notifier: PlanSeedFailureNotifier | None = field(
        default=None, repr=False
    )
    _stack: AsyncExitStack | None = field(default=None, repr=False)
    _running: _RunningService | None = field(default=None, repr=False)
    # Project-wide tmux sweep runs only after a successful start commits.
    _lifecycle_committed: bool = field(default=False, repr=False)

    def register_application_query(self, name: QueryName, handler: ApplicationHandler) -> None:
        """Register a feature use case at the closed application boundary."""
        self._application_queries[name] = handler

    def register_application_command(self, name: CommandName, handler: ApplicationHandler) -> None:
        """Register a feature use case at the closed application boundary."""
        self._application_commands[name] = handler

    def register_application_handlers(self) -> None:
        """Register feature-owned handlers at the closed application boundary."""
        from murder.app.service.handlers import register_all
        from murder.app.service.handlers.approvals import ApprovalUseCases
        from murder.app.service.handlers.workflows import WorkflowUseCases

        running = self._running
        if running is None:
            raise RuntimeError("service is not started")
        if self.read_model is None:
            raise RuntimeError("read model unavailable")
        if self.orchestrator is None:
            raise RuntimeError("orchestrator unavailable")
        register_all(
            self,
            projections=self.projection_providers,
            document_editors=running.editors,
            sessions=running.sessions,
            workflows=WorkflowUseCases(running.process.db),
            approvals=ApprovalUseCases(running.process.db),
            db=running.process.db,
            repo_root=self.repo_root,
            run_id=running.process.run_id,
            config=self.config,
            read_model=self.read_model,
            orchestrator=self.orchestrator,
            roster_service=running.roster,
            structured_decisions=running.structured_decisions,
        )

    def set_plan_seed_failure_notifier(
        self, notifier: PlanSeedFailureNotifier | None
    ) -> None:
        """Attach (or clear) the transport callback for planner-seed failures."""
        self._plan_seed_failure_notifier = notifier

    def schedule_plan_seed(
        self, plan_name: str, message: str, client_id: str | None
    ) -> None:
        """Schedule a planner seed; failures go to the attached notifier if any."""
        if self.background_tasks is None:
            raise RuntimeError("service is not started")

        async def notify_failure(error: str) -> None:
            if self._plan_seed_failure_notifier is not None:
                await self._plan_seed_failure_notifier(client_id, plan_name, error)

        self.background_tasks.schedule_plan_seed(
            plan_name, message, on_failure=notify_failure
        )

    @property
    def run_id(self) -> str:
        if self._running is None:
            raise RuntimeError("service is not started")
        return self._running.process.run_id

    @property
    def process(self) -> ProcessScope:
        if self._running is None:
            raise RuntimeError("service is not started")
        return self._running.process

    @property
    def repository_id(self) -> str:
        if self._running is None:
            raise RuntimeError("service is not started")
        return self._running.process.db.repository_id

    @property
    def observability_context(self) -> Context:
        """Per-host contextvars (run_id / repository_id / advanced_log)."""
        if self._running is None:
            raise RuntimeError("service is not started")
        return self._running.process.observability_context

    def has_live_agents(self) -> bool:
        """True when any registered agent is outside a terminal status."""
        from murder.runtime.agents.base import TERMINAL_STATUSES

        if self._running is None:
            return False
        return any(
            agent.status not in TERMINAL_STATUSES for agent in self._running.agents.all()
        )

    async def start(self) -> None:
        from murder.runtime.activity_dispatcher import (  # noqa: PLC0415
            build_default_activity_dispatcher,
        )
        from murder.runtime.trigger_dispatcher import (  # noqa: PLC0415
            build_default_trigger_dispatcher,
        )

        if self._stack is not None or self._running is not None:
            raise RuntimeError("RepositoryHost.start() cannot be called twice without stop()")

        activity_factory = self.activity_dispatcher_factory or build_default_activity_dispatcher
        trigger_factory = self.trigger_dispatcher_factory or (
            lambda connection: build_default_trigger_dispatcher(
                connection,
                repo_root=self.repo_root,
            )
        )

        stack = AsyncExitStack()
        await stack.__aenter__()
        try:
            process = await stack.enter_async_context(
                ProcessScope.open(self.config, self.repo_root)
            )
            # Wire the rest of the host under the ProcessScope observability
            # context so nested create_task calls inherit run_id / repository_id
            # / advanced_log without clobbering sibling hosts.
            await create_task_with_context(
                self._start_under_observability(
                    stack=stack,
                    process=process,
                    activity_factory=activity_factory,
                    trigger_factory=trigger_factory,
                ),
                name=f"repo-host-start:{process.repository_id}",
                context=process.observability_context,
            )
        except BaseException:
            self._lifecycle_committed = False
            with contextlib.suppress(Exception):
                await self._abort_partial_start(stack)
            raise

        self._stack = stack
        self._lifecycle_committed = True

    async def _start_under_observability(
        self,
        *,
        stack: AsyncExitStack,
        process: ProcessScope,
        activity_factory: ActivityDispatcherFactory,
        trigger_factory: TriggerDispatcherFactory,
    ) -> None:
        session_names = SessionNamePolicy.from_config(
            self.config,
            repository_id=process.repository_id,
        )

        async def _sweep() -> None:
            # Failed-start unwind must not project-sweep surviving Crow panes
            # that startup recovery deferred for post-socket reattach (§7.2).
            if not self._lifecycle_committed:
                return
            if process.is_external_stop_set():
                return
            with contextlib.suppress(Exception):
                await kill_project_tmux_sessions(session_names)

        stack.push_async_callback(_sweep)

        sessions = await stack.enter_async_context(SessionService.open(process.db))
        roster = RosterService(process.db)
        verified_factory = VerifiedControlFactory(db=process.db, sessions=sessions)
        agents = await stack.enter_async_context(
            AgentRuntime.open(
                db=process.db,
                config=self.config,
                repo_root=self.repo_root,
                roster=roster,
                events=process.events,
                run_id=process.run_id,
                advanced_log=process.advanced_log,
                preserve_tmux_on_close=process.is_external_stop_set,
                verified_control_factory=verified_factory,
                command_submitter=process.commands,
                sessions=sessions,
                lifecycle_events_enabled=(process.recorder_mode != "off"),
            )
        )

        sync = FilesystemSyncService.attach(self.repo_root, process.db)
        documents = DocumentService(
            repo_root=self.repo_root,
            db=process.db,
            plan_sync=sync.plan_sync,
            note_sync=sync.note_sync,
        )
        editors = DocumentEditorService(
            self.repo_root, documents, sessions=sessions
        )
        sync.seed()

        recovery = await run_startup_recovery(
            db=process.db,
            session_names=session_names,
        )

        # Prefer the daemon-shared registry so HarnessVersionProbeWorker
        # (process-scoped) broadcasts into every active host.
        harness_versions = self.harness_versions or HarnessVersionRegistry()
        self.harness_versions = harness_versions
        event_sink: AgentEventSink = LoggingAgentEventSink()
        structured_decisions = StructuredDecisionRouter(
            db=process.db,
            events=process.events,
            run_id=process.run_id,
            get_agent=agents.find,
        )
        agents.structured_decisions = structured_decisions

        self.read_model = ServiceReadModel(process.db, self.repo_root)
        self.fact_log = FactLog(process.db)
        self.projection_input_log = ProjectionInputLog(process.db)

        orchestrator = Orchestrator(
            config=self.config,
            user_config=self.user_config,
            repo_root=self.repo_root,
            db=process.db,
            run_id=process.run_id,
            events=process.events,
            commands=process.commands,
            event_sink=event_sink,
            agents=agents,
            session_names=session_names,
            plan_sync=sync.plan_sync,
        )
        agents.crow_ask_router.bind(orchestrator.route_crow_ask)
        self.orchestrator = orchestrator

        async def _send_parse_error(agent_id: str, message: str) -> None:
            await orchestrator.send_agent_message(
                agent_id,
                message,
                None,
                spawn_if_needed=False,
            )

        sync.set_parse_error_notifier(_send_parse_error)
        await stack.enter_async_context(sync.running())

        dispatchers = await stack.enter_async_context(
            DispatcherLoops.open(
                db=process.db,
                session_controllers=sessions.controllers,
                activity_factory=activity_factory,
                trigger_factory=trigger_factory,
            )
        )

        running = _RunningService(
            process=process,
            sessions=sessions,
            agents=agents,
            sync=sync,
            documents=documents,
            editors=editors,
            dispatchers=dispatchers,
            orchestrator=orchestrator,
            recovery=recovery,
            harness_versions=harness_versions,
            event_sink=event_sink,
            structured_decisions=structured_decisions,
            session_names=session_names,
            roster=roster,
        )
        # Expose lifecycle state for handler registration before workers start.
        self._running = running
        self.register_application_handlers()

        self.application_dispatcher = ApplicationDispatcher(
            queries=self._application_queries,
            commands=self._application_commands,
        )
        self.background_tasks = ServiceBackgroundTasks(
            repo_root=self.repo_root,
            db=process.db,
            agents=agents,
            orchestrator=orchestrator,
            recovery=recovery,
            advanced_log=process.advanced_log,
        )

        async def terminal_input(
            session_id: UUID,
            client_id: str,
            lease_id: UUID,
            fence: int,
            data: bytes,
        ) -> None:
            import base64

            record = sessions.store.get_session(session_id)
            if record is None or record.harness != "document_editor":
                raise ValueError("terminal input target is not a document editor")
            if record.transport is not SessionTransport.TMUX:
                raise ValueError("document editor does not expose a tmux terminal")
            controller = await sessions.controllers.get_or_create(record)
            await controller.execute(
                WriteTerminalInput(
                    operation_id=uuid4(),
                    lease_id=lease_id,
                    fence=fence,
                    encoding="base64",
                    data=base64.b64encode(data).decode("ascii"),
                ),
                principal=PrincipalRef(kind=PrincipalKind.CLIENT, id=client_id),
            )

        async def terminal_input_validator(
            session_id: UUID,
            client_id: str,
            lease_id: UUID,
            fence: int,
        ) -> None:
            record = sessions.store.get_session(session_id)
            if record is None or record.harness != "document_editor":
                raise ValueError("terminal input target is not a document editor")
            sessions.store.validate_writer_lease(
                session_id=session_id,
                lease_id=lease_id,
                fence=fence,
                holder=PrincipalRef(kind=PrincipalKind.CLIENT, id=client_id),
                required_mode=WriterMode.RAW_TERMINAL,
            )

        self.terminal_capture = sessions.capture_terminal
        self.terminal_output_open = sessions.open_terminal_output
        self.terminal_input = terminal_input
        self.terminal_input_validator = terminal_input_validator

        self.supervisor = await start_supervisor_workers(
            repo_root=self.repo_root,
            db=process.db,
            run_id=process.run_id,
            events=process.events,
            commands=process.commands,
            advanced_log=process.advanced_log,
            agents=agents,
            orchestrator=orchestrator,
        )
        self.background_tasks.start()

    async def _abort_partial_start(self, stack: AsyncExitStack) -> None:
        if self.background_tasks is not None:
            with contextlib.suppress(Exception):
                await self.background_tasks.stop()
            self.background_tasks = None
        if self.supervisor is not None:
            with contextlib.suppress(Exception):
                await self.supervisor.stop_all()
            self.supervisor = None
        with contextlib.suppress(Exception):
            await stack.aclose()
        self.read_model = None
        self.fact_log = None
        self.projection_input_log = None
        self.application_dispatcher = None
        self.terminal_capture = None
        self.terminal_output_open = None
        self.terminal_input = None
        self.terminal_input_validator = None
        self.orchestrator = None
        self._stack = None
        self._running = None
        self._lifecycle_committed = False

    def clear_shutdown_signal(self) -> None:
        """Make an imminent stop authoritative rather than signal-graceful."""
        if self._running is not None:
            self._running.process.clear_external_stop()

    async def stop(self) -> None:
        if self.background_tasks is not None:
            await self.background_tasks.stop()
            self.background_tasks = None

        if self.supervisor is not None:
            await self.supervisor.stop_all()
            self.supervisor = None

        if self._running is not None:
            # Authoritative stop for murder down (clear graceful signal).
            self._running.process.clear_external_stop()

        if self._stack is not None:
            with contextlib.suppress(Exception):
                await self._stack.aclose()
            self._stack = None

        self._running = None
        self._lifecycle_committed = False
        self.read_model = None
        self.fact_log = None
        self.projection_input_log = None
        self.application_dispatcher = None
        self.terminal_capture = None
        self.terminal_output_open = None
        self.terminal_input = None
        self.terminal_input_validator = None
        self.orchestrator = None

    async def __aenter__(self) -> RepositoryHost:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        await self.stop()


__all__ = ["RepositoryHost"]
