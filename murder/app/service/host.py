"""ServiceHost — backend composition root for the murder service process."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from murder.app.protocol.requests import CommandName, QueryName
from murder.app.protocol.subscriptions import ProjectionTopic
from murder.app.service.application import ApplicationDispatcher, ApplicationHandler
from murder.app.service.bootstrap import start_supervisor_workers
from murder.app.service.gateway import ApplicationGateway
from murder.app.service.read_model import ServiceReadModel
from murder.app.service.projection_registry import ProjectionProviderRegistry
from murder.app.service.runtime import (
    ActivityDispatcherFactory,
    Runtime,
    TriggerDispatcherFactory,
)
from murder.app.service.supervisor import Supervisor
from murder.app.service.socket_server import ApplicationSocketServer
from murder.facts.log import FactLog, ProjectionInputLog
from murder.config import Config
from murder.llm.harnesses.harnesses_doc import write_harnesses_doc
from murder.llm.harnesses.model_cache import refresh_and_persist_harness_models
from murder.observability.advanced_log import ParserRecord, current_advanced_log
from murder.runtime.orchestration.orchestrator import Orchestrator
from murder.runtime.workers.orchestrator_worker import dispatch_orchestrator_command
from murder.state.storage.paths import db_path
from murder.state.storage.service_registry import (
    project_session_name,
    remove_service_session,
    write_service_session,
)
from murder.roster.service import register_projection_provider
from murder.usage_sample_command import run_service_usage_poll_loop

LOGGER = logging.getLogger(__name__)

# Cadence for the Transit git-graph fingerprint poll (branch HEAD movement).
TRANSIT_POLL_INTERVAL_S = 4.0

# Caps how many surviving-crow reattaches may poll harness/tmux state at once.
# Each reattach can block on a ready-poll for up to 240s; gathering every
# survivor unbounded recreates the boot-time file-descriptor storm the deferred
# background design exists to avoid, so we throttle to a small handful.
REATTACH_CONCURRENCY = 4


@dataclass(frozen=True)
class CapturedTerminalFrame:
    data: str
    columns: int
    rows: int

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

    # god-debt: the background loops (_run_projection_poll_loop,
    # _run_transit_poll_loop, _reattach_surviving_crows,
    # _ensure_startup_rogue_safely, _persist_catalog_then_write_models_doc) are still
    # inline. Deferred follow-up (godslayer plan, Phase 1b): move them to a
    # BackgroundLoops collaborator owned by the host. Left inline because they
    # have no start/stop test net and are perpetual loops, so the extraction
    # needs real-boot verification rather than a unit pass.
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
    _usage_poll_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _projection_poll_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _transit_poll_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _model_catalog_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _reattach_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _application_queries: dict[QueryName, ApplicationHandler] = field(
        default_factory=dict, repr=False
    )
    _application_commands: dict[CommandName, ApplicationHandler] = field(
        default_factory=dict, repr=False
    )
    _service_session_name: str | None = field(default=None, repr=False)

    async def _capture_tmux_frame(
        self,
        target_id: str | None = None,
    ) -> CapturedTerminalFrame:
        """Capture a persisted session UUID, or an explicit legacy agent.

        UUID attachment resolves through ``harness_sessions.transport_ref``;
        the durable session identity is never treated as a tmux name. Existing
        agent-id callers remain a deliberate compatibility branch until every
        live legacy agent is registered. ``None`` selects the supervisor pane.
        """
        from murder.runtime.terminal import tmux

        session = project_session_name(self.repo_root)
        if target_id is not None:
            if self.runtime is None:
                raise RuntimeError("service not started")
            try:
                persisted_id = UUID(target_id)
            except ValueError:
                agent = self.runtime.agents.get_agent(target_id)
                agent_session = getattr(agent, "session", None)
                if agent_session is None:
                    raise ValueError(
                        f"no live agent session for {target_id!r}"
                    ) from None
                session = str(agent_session)
            else:
                if self.runtime.db is None:
                    raise RuntimeError("service database is unavailable")
                row = self.runtime.db.execute(
                    """
                    SELECT transport, transport_ref
                    FROM harness_sessions
                    WHERE session_id = ?
                    """,
                    (str(persisted_id),),
                ).fetchone()
                if row is None:
                    raise ValueError(f"persisted session {persisted_id} does not exist")
                if str(row["transport"]) != "tmux":
                    raise ValueError(
                        f"session {persisted_id} does not expose a tmux terminal"
                    )
                session = str(row["transport_ref"])
        frame = await tmux.capture_viewport(session, escapes=True)
        columns, rows = await tmux.pane_dimensions(session)
        return CapturedTerminalFrame(data=frame, columns=columns, rows=rows)

    def register_application_query(self, name: QueryName, handler: ApplicationHandler) -> None:
        """Register a feature use case at the closed application boundary."""
        self._application_queries[name] = handler

    def register_application_command(self, name: CommandName, handler: ApplicationHandler) -> None:
        """Register a feature use case at the closed application boundary."""
        self._application_commands[name] = handler

    def register_application_handlers(self) -> None:
        """Register feature-owned handlers at the closed application boundary."""
        from murder.app.service.handlers import register_all

        register_all(self)

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

        register_projection_provider(
            self.projection_providers,
            self.runtime.roster,
            self.runtime.db,
        )
        assert self.read_model is not None
        # Composition root wiring only: each provider calls the feature's own
        # read surface.  The socket server merely asks the registry; it never
        # switches on projection names or imports feature domains.
        self.projection_providers.register(
            ProjectionTopic.CONVERSATIONS,
            lambda: self.read_model.get_conversations_snapshot().model_dump(mode="json"),
        )
        self.projection_providers.register(
            ProjectionTopic.SCHEDULE,
            lambda: self.read_model.get_schedule_snapshot().model_dump(mode="json"),
        )
        for topic, query in (
            (ProjectionTopic.FAVORITES, QueryName.FAVORITES_GET),
            (ProjectionTopic.TEMPLATES, QueryName.TEMPLATES_GET),
            (ProjectionTopic.THEMES, QueryName.THEMES_GET),
            (ProjectionTopic.WORKFLOWS, QueryName.WORKFLOWS_GET),
            (ProjectionTopic.SETTINGS, QueryName.SETTINGS_GET),
        ):
            handler = self._application_queries[query]
            self.projection_providers.register(topic, lambda handler=handler: handler({}))
        self.fact_log = FactLog(self.runtime.db)
        self.projection_input_log = ProjectionInputLog(self.runtime.db)

        self.orchestrator = Orchestrator(self.runtime)
        self.runtime.crow_ask_router = self.orchestrator.route_crow_ask
        # Route malformed-artifact parse errors back to the owning agent now
        # that the orchestrator (which delivers `agent.message`) exists.
        if self.runtime._sync is not None:
            orch = self.orchestrator

            async def _send_parse_error(agent_id: str, message: str) -> None:
                await orch.send_agent_message(agent_id, message, None, spawn_if_needed=False)

            self.runtime._sync.set_parse_error_notifier(_send_parse_error)

        orchestrator = self.orchestrator

        orchestrator_commands = (
            CommandName.AGENT_INTERRUPT,
            CommandName.AGENT_MESSAGE,
            CommandName.AGENT_RESUME_FROM_HISTORY,
            CommandName.AGENT_SEND_KEY,
            CommandName.AGENT_STOP,
            CommandName.CROW_RENAME_ROGUE,
            CommandName.CROW_RESET,
            CommandName.CROW_SPAWN_ROGUE,
            CommandName.HISTORY_DISMISS,
            CommandName.NOTETAKER_CAPTURE_SUBMIT,
            CommandName.PLAN_RENAME,
            CommandName.PLANNER_SPAWN,
            CommandName.TICKET_QUICK_CREATE,
        )
        for command_name in orchestrator_commands:
            async def _execute(body: dict[str, Any], name: CommandName = command_name) -> dict[str, Any]:
                return await dispatch_orchestrator_command(orchestrator, name, body)

            self.register_application_command(command_name, _execute)

        def _set_scheduler_steering(body: dict[str, Any]) -> dict[str, Any]:
            from murder.app.service.scheduler_steering import set_steering

            runtime = self.runtime
            if runtime is None or runtime.db is None:
                raise RuntimeError("service runtime is unavailable")
            harness = body.get("harness")
            steering = body.get("steering")
            if not isinstance(harness, str) or not isinstance(steering, str):
                raise ValueError("scheduler.set_steering requires harness and steering strings")
            return set_steering(runtime.db, harness=harness, steering=steering)

        async def _sample_usage(body: dict[str, Any]) -> dict[str, Any]:
            from murder.app.service.usage_sampling import sample_usage

            runtime = self.runtime
            if runtime is None or runtime.db is None:
                raise RuntimeError("service runtime is unavailable")
            raw_modes = body.get("modes")
            if raw_modes is not None and not isinstance(raw_modes, list):
                raise ValueError("state.harness_usage.sample modes must be a list when provided")
            modes = {str(mode) for mode in raw_modes} if raw_modes is not None else None
            return await sample_usage(repo_root=self.repo_root, db=runtime.db, modes=modes)

        self.register_application_command(CommandName.SCHEDULER_SET_STEERING, _set_scheduler_steering)
        self.register_application_command(CommandName.HARNESS_USAGE_SAMPLE, _sample_usage)

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
            terminal_capture=self._capture_tmux_frame,
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

        # Startup recovery: reattach handlers to crows whose tmux session
        # survived a service restart so DONE is consumed and the ticket
        # finishes. Each reattach can block on a harness ready-poll (up to
        # 240s), so this runs as a best-effort background task launched AFTER
        # the socket is open — it must never delay boot — and the reattaches
        # run concurrently rather than sequentially.
        self._reattach_task = asyncio.create_task(
            self._reattach_surviving_crows(), name="crow-reattach"
        )

        LOGGER.info("application websocket listener on ws://%s:%d/api/ws", *self.websocket_bound)

        self.supervisor = await start_supervisor_workers(
            repo_root=self.repo_root,
            runtime=self.runtime,
            orchestrator=self.orchestrator,
            bus=self.runtime.bus,
        )
        self._startup_rogue_task = asyncio.create_task(
            self._ensure_startup_rogue_safely(), name="startup-rogue-ensure"
        )
        self._model_catalog_task = asyncio.create_task(
            self._persist_catalog_then_write_models_doc(), name="startup-model-catalog"
        )
        self._usage_poll_task = asyncio.create_task(
            run_service_usage_poll_loop(self.repo_root, self.runtime.db),
            name="usage-sample-poll",
        )
        self._projection_poll_task = asyncio.create_task(
            self._run_projection_poll_loop(), name="transcript-projection-poll"
        )
    async def _ensure_startup_rogue_safely(self) -> None:
        """Best-effort: spawn the user's configured Startup Rogue on boot.

        Idempotent (the orchestrator reuses a live one); never fatal to startup —
        a spawn failure is logged and swallowed so the daemon still comes up.
        """
        try:
            await self.orchestrator.ensure_startup_rogue()
        except Exception:
            LOGGER.error("ensure_startup_rogue failed", exc_info=True)

    async def _persist_catalog_then_write_models_doc(self) -> None:
        """Persist configured model catalogs, then write the settings document."""
        db = self.runtime.db if self.runtime is not None else None
        await refresh_and_persist_harness_models(self.repo_root, db)
        write_harnesses_doc(self.repo_root)

    async def _run_projection_poll_loop(self) -> None:
        """Single service-owned ticker that projects every harness-backed agent's
        pane into the conversation store. One loop for crows, rogues,
        collaborators, and planners alike — projection is a universal per-agent
        concern, decoupled from ticket orchestration (CrowHandler) so ticketless
        rogues and collaborators are covered too."""
        from murder.runtime.agents.base import (
            PROJECTION_INTERVAL_S,
            HarnessBackedAgent,
        )
        from murder.runtime.terminal import tmux

        # First-failure visibility: a projection exception repeating every tick
        # silently freezes an agent's live_state (and with it queued-message
        # delivery), so log the FIRST failure per agent at WARNING with the
        # traceback; subsequent identical-cadence failures stay at DEBUG.
        warned_agents: set[str] = set()
        while True:
            runtime = self.runtime
            if runtime is not None:
                for agent in runtime.agents.all_agents():
                    if not isinstance(agent, HarnessBackedAgent):
                        continue
                    try:
                        await agent.project_once()
                    except tmux.TmuxError:
                        LOGGER.debug(
                            "projection tick: tmux error for %s (session=%s)",
                            agent.id,
                            getattr(agent, "session", None),
                            exc_info=True,
                        )
                    except Exception:
                        if agent.id not in warned_agents:
                            warned_agents.add(agent.id)
                            LOGGER.warning(
                                "projection tick failed for %s (suppressing repeats)",
                                agent.id,
                                exc_info=True,
                            )
                        else:
                            LOGGER.debug("projection tick failed for %s", agent.id, exc_info=True)
                    else:
                        warned_agents.discard(agent.id)
                        # Boundary #5b: record the derived projection/live-state
                        # for the flight recorder. The dedup_hash over
                        # (live_state, queued) lets the ChangeGate write only
                        # when an agent's state actually changed since last tick,
                        # so idle no-op polls are deduped, not one row each.
                        live_state = agent._current_live_state()
                        queued = agent.pending_message
                        choices = ["<choice-prompt>"] if live_state == "awaiting_approval" else None
                        current_advanced_log().record_parser(
                            ParserRecord(
                                session=getattr(agent, "session", None),
                                live_state=live_state,
                                parsed={"agent_id": agent.id, "queued": queued},
                                choices=choices,
                                dedup_hash=hashlib.sha1(
                                    f"{agent.id}|{live_state}|{queued}".encode()
                                ).hexdigest(),
                            )
                        )
            await asyncio.sleep(PROJECTION_INTERVAL_S)

    async def _reattach_surviving_crows(self) -> None:
        """Best-effort startup recovery, run as a background task after the
        socket is open so it never delays boot. Reattaches handlers to crows
        whose tmux session survived a service restart, running the reattaches
        with bounded concurrency; each can block on a harness ready-poll (up to
        240s), so we cap the simultaneous polls (REATTACH_CONCURRENCY) to avoid
        an FD storm at boot, and a single failure must not kill the others or
        crash boot."""
        report = getattr(self.runtime, "startup_reconcile_report", None)
        if not report or not report.crows_to_reattach:
            return

        # Throttle the ready-polls: only REATTACH_CONCURRENCY reattaches may hold
        # harness/tmux file descriptors open at any one time.
        sem = asyncio.Semaphore(REATTACH_CONCURRENCY)

        async def _reattach_one(tid: str, crow_session: str) -> None:
            async with sem:
                try:
                    await self.orchestrator.reattach_crow(tid, crow_session)
                    LOGGER.info("reattached crow handler for %s (session %s)", tid, crow_session)
                except Exception:
                    LOGGER.error("failed to reattach crow for %s", tid, exc_info=True)

        try:
            await asyncio.gather(
                *(
                    _reattach_one(tid, crow_session)
                    for tid, crow_session in report.crows_to_reattach
                )
            )
        except Exception:
            LOGGER.error("crow reattach task failed", exc_info=True)

    async def run_until_signal(self) -> None:
        if self.runtime is None:
            raise RuntimeError("ServiceHost.start() must be called first")
        await self.runtime.run_until_signal()

    async def stop(self) -> None:
        if self._usage_poll_task is not None:
            self._usage_poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._usage_poll_task
            self._usage_poll_task = None

        if self._projection_poll_task is not None:
            self._projection_poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._projection_poll_task
            self._projection_poll_task = None

        if self._transit_poll_task is not None:
            self._transit_poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._transit_poll_task
            self._transit_poll_task = None

        if self._model_catalog_task is not None:
            self._model_catalog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._model_catalog_task
            self._model_catalog_task = None

        if self._reattach_task is not None:
            self._reattach_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reattach_task
            self._reattach_task = None

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
            try:
                self.runtime._external_stop.clear()
            except Exception:  # noqa: BLE001
                LOGGER.debug(
                    "failed to clear runtime._external_stop during shutdown", exc_info=True
                )
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
