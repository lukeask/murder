"""ServiceHost — backend composition root for the murder service process."""

from __future__ import annotations

import contextlib
import logging
from contextlib import AsyncExitStack
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
from murder.app.service.document_access import DocumentAccess
from murder.app.service.document_editor_sessions import DocumentEditorSessions
from murder.app.service.filesystem_sync import FilesystemSyncService
from murder.app.service.gateway import ApplicationGateway
from murder.app.service.process_scope import ProcessScope
from murder.app.service.projection_registry import ProjectionProviderRegistry
from murder.app.service.read_model import ServiceReadModel
from murder.app.service.runtime import Runtime
from murder.app.service.runtime_lifecycle import kill_project_tmux_sessions
from murder.app.service.socket_server import ApplicationSocketServer
from murder.app.service.startup_recovery import StartupRecoveryResult, run_startup_recovery
from murder.app.service.supervisor import Supervisor
from murder.config import Config
from murder.facts.log import FactLog, ProjectionInputLog
from murder.llm.harnesses.versioning import HarnessVersionRegistry
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
from murder.state.storage.service_registry import (
    remove_service_session,
    write_service_session,
)

LOGGER = logging.getLogger(__name__)


@dataclass
class _RunningService:
    """Private host typestate. Never injected into child components."""

    process: ProcessScope
    sessions: SessionService
    agents: AgentRuntime
    sync: FilesystemSyncService
    documents: DocumentAccess
    editors: DocumentEditorSessions
    dispatchers: DispatcherLoops
    orchestrator: Orchestrator
    recovery: StartupRecoveryResult
    harness_versions: HarnessVersionRegistry
    event_sink: AgentEventSink
    structured_decisions: StructuredDecisionRouter
    session_names: SessionNamePolicy
    runtime: Runtime  # temporary facade for handlers/bootstrap (Phase 5 removes)


@dataclass
class ServiceHost:
    """Wires runtime, application services, and the application socket server.

    Responsibility (keep it this narrow): the process COMPOSITION ROOT and
    lifecycle owner. ``start``/``stop`` wire the collaborators and own the
    background tasks. ``register_application_handlers`` just delegates to the
    ``handlers/`` package. This class deliberately holds NO request logic.
    """

    config: Config
    repo_root: Path
    websocket_host: str = "127.0.0.1"
    websocket_port: int = 0
    activity_dispatcher_factory: ActivityDispatcherFactory | None = None
    trigger_dispatcher_factory: TriggerDispatcherFactory | None = None
    runtime: Runtime | None = None
    read_model: ServiceReadModel | None = None
    fact_log: FactLog | None = None
    projection_input_log: ProjectionInputLog | None = None
    projection_providers: ProjectionProviderRegistry = field(
        default_factory=ProjectionProviderRegistry, repr=False
    )
    orchestrator: Orchestrator | None = None
    supervisor: Supervisor | None = None
    socket_server: ApplicationSocketServer | None = None
    websocket_bound: tuple[str, int] | None = None
    background_tasks: ServiceBackgroundTasks | None = field(default=None, repr=False)
    _application_queries: dict[QueryName, ApplicationHandler] = field(
        default_factory=dict, repr=False
    )
    _application_commands: dict[CommandName, ApplicationHandler] = field(
        default_factory=dict, repr=False
    )
    _service_session_name: str | None = field(default=None, repr=False)
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

        if self.runtime is None:
            raise RuntimeError("service runtime is unavailable")
        if self.runtime.sessions is None:
            raise RuntimeError("session service is unavailable")
        register_all(
            self,
            projections=self.projection_providers,
            effects=self.runtime,
            sessions=self.runtime.sessions,
        )

    async def start(self) -> None:
        from murder.runtime.activity_dispatcher import (  # noqa: PLC0415
            build_default_activity_dispatcher,
        )
        from murder.runtime.trigger_dispatcher import (  # noqa: PLC0415
            build_default_trigger_dispatcher,
        )
        from murder.user_config import ensure_user_themes, load_user_config  # noqa: PLC0415

        if self._stack is not None or self._running is not None:
            raise RuntimeError("ServiceHost.start() cannot be called twice without stop()")

        ensure_user_themes()
        try:
            user_cfg = load_user_config()
        except Exception:
            user_cfg = None

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
            session_names = SessionNamePolicy.from_config(self.config)

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
                    roster=roster,
                    events=process.events,
                    run_id=process.run_id,
                    advanced_log=process.advanced_log,
                    preserve_tmux_on_close=process.is_external_stop_set,
                    verified_control_factory=verified_factory,
                    lifecycle_events_enabled=(process.recorder_mode != "off"),
                )
            )
            agents.command_submitter = process.commands
            agents.sessions = sessions

            sync = FilesystemSyncService.attach(self.repo_root, process.db)
            documents = DocumentAccess(
                self.repo_root,
                process.db,
                plan_sync=sync.plan_sync,
                note_sync=sync.note_sync,
            )
            editors = DocumentEditorSessions(
                self.repo_root, documents, sessions=sessions
            )
            sync.seed()

            recovery = await run_startup_recovery(
                db=process.db,
                repo_root=self.repo_root,
                agents=agents,
                sessions=sessions,
            )

            harness_versions = HarnessVersionRegistry()
            event_sink: AgentEventSink = LoggingAgentEventSink()
            structured_decisions = StructuredDecisionRouter(
                db=process.db,
                events=process.events,
                run_id=process.run_id,
                get_agent=agents.find,
            )

            # Temporary Runtime facade for handlers / bootstrap (Phase 5 removes).
            runtime = Runtime(
                self.config,
                self.repo_root,
                user_cfg=user_cfg,
                activity_dispatcher_factory=activity_factory,
                trigger_dispatcher_factory=trigger_factory,
            )
            runtime.bind_process(process)
            runtime.sessions = sessions
            runtime.agents = agents
            runtime.roster = roster
            runtime._sync = sync  # noqa: SLF001
            runtime.plan_sync = sync.plan_sync
            runtime.note_sync = sync.note_sync
            runtime.notetaker_context_sync = sync.notetaker_context_sync
            runtime.ticket_sync = sync.ticket_sync
            runtime.report_sync = sync.report_sync
            runtime.documents = documents
            runtime.document_editors = editors
            runtime.startup_reconcile_report = recovery
            runtime.structured_decisions = structured_decisions
            runtime.harness_versions = harness_versions
            runtime.event_sink = event_sink
            runtime.session_names = session_names

            self.runtime = runtime
            self.read_model = ServiceReadModel(process.db, self.repo_root)
            self.register_application_handlers()

            self.fact_log = FactLog(process.db)
            self.projection_input_log = ProjectionInputLog(process.db)

            orchestrator = Orchestrator(
                config=self.config,
                user_config=user_cfg,
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
            agents.crow_ask_router = orchestrator.route_crow_ask
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
            runtime._dispatchers = dispatchers  # noqa: SLF001
            runtime.activity_dispatcher = dispatchers.activity_dispatcher
            runtime.trigger_dispatcher = dispatchers.trigger_dispatcher

            from murder.app.service.handlers import orchestration, scheduler, usage

            orchestration.register(self, orchestrator)
            scheduler.register(self, runtime)
            usage.register(self, runtime)

            application = ApplicationDispatcher(
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

            def schedule_plan_seed(plan_name: str, message: str, client_id: str | None) -> None:
                assert self.background_tasks is not None

                async def notify_failure(error: str) -> None:
                    if self.socket_server is not None:
                        await self.socket_server.notify_plan_seed_failed(
                            client_id, plan_name, error
                        )

                self.background_tasks.schedule_plan_seed(
                    plan_name, message, on_failure=notify_failure
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

            self.socket_server = ApplicationSocketServer(
                gateway=ApplicationGateway(
                    application, schedule_plan_seed=schedule_plan_seed
                ),
                facts=self.fact_log,
                projection_inputs=self.projection_input_log,
                providers=self.projection_providers,
                run_id=str(process.run_id),
                terminal_capture=sessions.capture_terminal,
                terminal_output_open=sessions.open_terminal_output,
                terminal_input=terminal_input,
                terminal_input_validator=terminal_input_validator,
                assets_dir=(self.repo_root / "webui" / "dist"),
            )
            self.websocket_bound = await self.socket_server.start(
                host=self.websocket_host, port=self.websocket_port
            )
            host, port = self.websocket_bound
            session = write_service_session(self.repo_root, f"ws://{host}:{port}/api/ws")
            self._service_session_name = session.name

            LOGGER.info(
                "application websocket listener on ws://%s:%d/api/ws", *self.websocket_bound
            )

            self.supervisor = await start_supervisor_workers(
                repo_root=self.repo_root,
                runtime=runtime,
                orchestrator=orchestrator,
                events=process.events,
            )
            self.background_tasks.start()

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
                runtime=runtime,
            )
        except BaseException:
            self._lifecycle_committed = False
            with contextlib.suppress(Exception):
                await self._abort_partial_start(stack)
            raise

        self._stack = stack
        self._running = running
        self._lifecycle_committed = True

    async def _abort_partial_start(self, stack: AsyncExitStack) -> None:
        if self.background_tasks is not None:
            with contextlib.suppress(Exception):
                await self.background_tasks.stop()
            self.background_tasks = None
        if self.supervisor is not None:
            with contextlib.suppress(Exception):
                await self.supervisor.stop_all()
            self.supervisor = None
        if self.socket_server is not None:
            with contextlib.suppress(Exception):
                await self.socket_server.stop()
            self.socket_server = None
        self.websocket_bound = None
        if self._service_session_name is not None:
            with contextlib.suppress(Exception):
                remove_service_session(self._service_session_name)
            self._service_session_name = None
        with contextlib.suppress(Exception):
            await stack.aclose()
        self.runtime = None
        self.read_model = None
        self.fact_log = None
        self.projection_input_log = None
        self.orchestrator = None
        self._stack = None
        self._running = None
        self._lifecycle_committed = False

    async def run_until_signal(self) -> None:
        if self._running is None:
            raise RuntimeError("call ServiceHost.start() first")
        await self._running.process.wait_for_signal()

    async def stop(self) -> None:
        if self.background_tasks is not None:
            await self.background_tasks.stop()
            self.background_tasks = None

        if self.supervisor is not None:
            await self.supervisor.stop_all()
            self.supervisor = None

        if self.socket_server is not None:
            with contextlib.suppress(FileNotFoundError, OSError):
                await self.socket_server.stop()
            self.socket_server = None

        if self._service_session_name is not None:
            remove_service_session(self._service_session_name)
            self._service_session_name = None

        if self._running is not None:
            # Authoritative stop for murder down (clear graceful signal).
            self._running.process.clear_external_stop()

        if self._stack is not None:
            with contextlib.suppress(Exception):
                await self._stack.aclose()
            self._stack = None

        if self.runtime is not None:
            self.runtime._clear_local_refs()  # noqa: SLF001
            self.runtime = None
        self._running = None
        self._lifecycle_committed = False
        self.read_model = None
        self.fact_log = None
        self.projection_input_log = None
        self.orchestrator = None
        self.websocket_bound = None

    async def __aenter__(self) -> ServiceHost:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        await self.stop()


__all__ = ["ServiceHost"]
