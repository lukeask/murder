"""ServiceHost — backend composition root for the murder service process."""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

from murder.app.protocol.requests import CommandName, QueryName
from murder.app.service.application import ApplicationDispatcher, ApplicationHandler
from murder.app.service.background_tasks import ServiceBackgroundTasks
from murder.app.service.bootstrap import start_supervisor_workers
from murder.app.service.gateway import ApplicationGateway
from murder.app.service.projection_registry import ProjectionProviderRegistry
from murder.app.service.read_model import ServiceReadModel
from murder.app.service.runtime import (
    ActivityDispatcherFactory,
    Runtime,
    TriggerDispatcherFactory,
)
from murder.app.service.socket_server import ApplicationSocketServer
from murder.app.service.supervisor import Supervisor
from murder.config import Config
from murder.facts.log import FactLog, ProjectionInputLog
from murder.runtime.orchestration.orchestrator import Orchestrator
from murder.state.storage.paths import db_path
from murder.state.storage.service_registry import (
    remove_service_session,
    write_service_session,
)

LOGGER = logging.getLogger(__name__)

@dataclass
class ServiceHost:
    """Wires runtime, application services, and the application socket server.

    Responsibility (keep it this narrow): the process COMPOSITION ROOT and
    lifecycle owner. ``start``/``stop`` wire the collaborators and own the
    background tasks; ``register_application_handlers`` just delegates to the
    ``handlers/`` package. This class deliberately holds NO request logic.

    Extending the application surface — DO NOT add a handler closure here. Inline
    accretion is exactly what doubled this file to ~1100 lines before it was
    slain back to a composition root. Instead:
      • New method in an existing namespace → add it in
        ``murder/app/service/handlers/<namespace>.py`` (e.g. a new ``state.*``
        read goes in ``handlers/state.py``) and register it inside that
        module's ``register(host)``.
      • A new namespace → add ``handlers/<name>.py`` exposing ``register(host)``
        and list it once in ``handlers/__init__.py::register_all``.
      • Shared deps (read_model/orchestrator access, threading, DTO wrapping)
        live in ``handlers/_common.py`` — reuse them, don't re-roll.
    Ousterhout: each handler module is a deep module behind a one-line
    ``register`` seam, so host.py stays shallow-but-small (pure wiring). Adding
    logic here trades a narrow interface for a god class — don't.

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
        register_all(
            self,
            projections=self.projection_providers,
            effects=self.runtime,
        )

    async def start(self) -> None:
        from murder.runtime.activity_dispatcher import (  # noqa: PLC0415
            build_default_activity_dispatcher,
        )
        from murder.runtime.trigger_dispatcher import (  # noqa: PLC0415
            build_default_trigger_dispatcher,
        )
        from murder.user_config import ensure_user_themes, load_user_config  # noqa: PLC0415

        ensure_user_themes()
        try:
            user_cfg = load_user_config()
        except Exception:
            user_cfg = None
        # Bringup is multi-step (runtime, socket, TCP, workers, poll tasks,
        # question listener). If any step throws, the runtime is already
        # started (flock held, tmux reconciled, agents reattached) and tasks
        # may already exist -- nothing would call ``stop()`` because
        # ``__aexit__`` only fires after ``start()`` returns. Roll back by
        # running the (idempotent, None-tolerant) ``stop()`` on any failure
        # before re-raising, so a half-started daemon never leaves the lock
        # held or tmux sessions orphaned.
        try:
            self.runtime = Runtime(
                self.config,
                self.repo_root,
                user_cfg=user_cfg,
                activity_dispatcher_factory=(
                    self.activity_dispatcher_factory or build_default_activity_dispatcher
                ),
                trigger_dispatcher_factory=(
                    self.trigger_dispatcher_factory
                    or (
                        lambda connection: build_default_trigger_dispatcher(
                            connection,
                            repo_root=self.repo_root,
                        )
                    )
                ),
            )
            await self.runtime.start()
            if self.runtime.db is None or self.runtime.bus is None or self.runtime.run_id is None:
                raise RuntimeError("runtime failed to initialize db/bus/run_id")
            self.read_model = ServiceReadModel(db_path(self.repo_root))

            await self._start_inner()
        except BaseException:
            with contextlib.suppress(Exception):
                await self.stop()
            raise

    async def _start_inner(self) -> None:
        assert self.runtime is not None and self.runtime.bus is not None
        assert self.runtime.db is not None and self.runtime.run_id is not None
        self.register_application_handlers()

        self.fact_log = FactLog(self.runtime.db)
        self.projection_input_log = ProjectionInputLog(self.runtime.db)

        self.orchestrator = Orchestrator(self.runtime)
        self.runtime.crow_ask_router = self.orchestrator.route_crow_ask
        # Route malformed-artifact parse errors back to the owning agent now
        # that the orchestrator (which delivers `agent.message`) exists.
        orchestrator_for_parse_errors = self.orchestrator

        async def _send_parse_error(agent_id: str, message: str) -> None:
            await orchestrator_for_parse_errors.send_agent_message(
                agent_id,
                message,
                None,
                spawn_if_needed=False,
            )

        self.runtime.configure_parse_error_notifier(_send_parse_error)

        from murder.app.service.handlers import orchestration, scheduler, usage

        orchestration.register(self, self.orchestrator)
        scheduler.register(self, self.runtime)
        usage.register(self, self.runtime)

        application = ApplicationDispatcher(
            queries=self._application_queries,
            commands=self._application_commands,
        )
        self.socket_server = ApplicationSocketServer(
            gateway=ApplicationGateway(application),
            facts=self.fact_log,
            projection_inputs=self.projection_input_log,
            providers=self.projection_providers,
            run_id=str(self.runtime.run_id),
            terminal_capture=self.runtime.capture_terminal_frame,
            assets_dir=(self.repo_root / "webui" / "dist"),
        )
        self.websocket_bound = await self.socket_server.start(
            host=self.websocket_host, port=self.websocket_port
        )
        host, port = self.websocket_bound
        session = write_service_session(
            self.repo_root, f"ws://{host}:{port}/api/ws"
        )
        self._service_session_name = session.name

        LOGGER.info("application websocket listener on ws://%s:%d/api/ws", *self.websocket_bound)

        self.supervisor = await start_supervisor_workers(
            repo_root=self.repo_root,
            runtime=self.runtime,
            orchestrator=self.orchestrator,
            bus=self.runtime.bus,
        )
        self.background_tasks = ServiceBackgroundTasks(
            repo_root=self.repo_root,
            runtime=self.runtime,
            orchestrator=self.orchestrator,
        )
        self.background_tasks.start()

    async def run_until_signal(self) -> None:
        if self.runtime is None:
            raise RuntimeError("ServiceHost.start() must be called first")
        await self.runtime.run_until_signal()

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

        if self.runtime is not None:
            self.runtime.clear_shutdown_signal()
            await self.runtime.stop()
            self.runtime = None

        self.read_model = None
        self.fact_log = None
        self.projection_input_log = None
        self.orchestrator = None

    async def __aenter__(self) -> ServiceHost:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        await self.stop()


__all__ = ["ServiceHost"]
