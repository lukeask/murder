"""ServiceHost — backend composition root for the murder service process."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from murder.app.service.bootstrap import start_supervisor_workers
from murder.app.service.read_model import ServiceReadModel
from murder.app.service.runtime import Runtime
from murder.app.service.supervisor import Supervisor
from murder.bus.broker import DurableBroker
from murder.bus.transport_socket import SocketBusServer, default_socket_path
from murder.config import Config
from murder.llm.harnesses.harnesses_doc import write_harnesses_doc
from murder.llm.harnesses.model_cache import refresh_and_persist_harness_models
from murder.observability.advanced_log import ParserRecord, current_advanced_log
from murder.runtime.orchestration.orchestrator import Orchestrator
from murder.state.storage.paths import db_path
from murder.state.storage.service_registry import (
    project_session_name,
    remove_service_session,
    write_service_session,
)
from murder.usage_sample_command import run_service_usage_poll_loop

LOGGER = logging.getLogger(__name__)

# Cadence for the Transit git-graph fingerprint poll (branch HEAD movement).
TRANSIT_POLL_INTERVAL_S = 4.0

# Caps how many surviving-crow reattaches may poll harness/tmux state at once.
# Each reattach can block on a ready-poll for up to 240s; gathering every
# survivor unbounded recreates the boot-time file-descriptor storm the deferred
# background design exists to avoid, so we throttle to a small handful.
REATTACH_CONCURRENCY = 4

RpcHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]]


@dataclass
class ServiceHost:
    """Wires runtime, bus broker, socket server, orchestrator, and supervisor.

    Responsibility (keep it this narrow): the process COMPOSITION ROOT and
    lifecycle owner. ``start``/``stop`` wire the collaborators and own the
    background tasks; ``register_default_rpc_handlers`` just delegates to the
    ``handlers/`` package. This class deliberately holds NO request logic.

    Extending the RPC surface — DO NOT add a handler closure here. Inline
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
    # _ensure_startup_rogue_safely, _discover_then_write_models_doc) are still
    # inline. Deferred follow-up (godslayer plan, Phase 1b): move them to a
    # BackgroundLoops collaborator owned by the host. Left inline because they
    # have no start/stop test net and are perpetual loops, so the extraction
    # needs real-boot verification rather than a unit pass.
    """

    config: Config
    repo_root: Path
    socket_path: Path = field(default_factory=default_socket_path)
    tcp_port: int | None = None
    runtime: Runtime | None = None
    read_model: ServiceReadModel | None = None
    broker: DurableBroker | None = None
    orchestrator: Orchestrator | None = None
    supervisor: Supervisor | None = None
    socket_server: SocketBusServer | None = None
    tcp_bound: tuple[str, int] | None = None
    _usage_poll_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _projection_poll_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _transit_poll_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _model_discovery_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _reattach_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _rpc_handlers: dict[str, RpcHandler] = field(default_factory=dict, repr=False)
    _service_session_name: str | None = field(default=None, repr=False)

    async def _capture_tmux_frame(self, agent_id: str | None = None) -> str:
        """Return the current ANSI frame for *agent_id*'s tmux session.

        Called by the ``tmux.frame`` stream on every capture tick. The raw
        view exists as the backup when transcript parsing breaks, so it must
        show the pane of the crow the user is looking at — each agent runs in
        its own tmux session, found via the registry. Without an agent (or for
        an unknown id) fall back to the deterministic project session name,
        which works as soon as the host is constructed.
        """
        from murder.runtime.terminal import tmux

        session = project_session_name(self.repo_root)
        if agent_id is not None:
            if self.runtime is None:
                raise RuntimeError("service not started")
            agent = self.runtime.agents.get_agent(agent_id)
            agent_session = getattr(agent, "session", None)
            if agent_session is None:
                raise ValueError(f"no live agent session for {agent_id!r}")
            session = agent_session
        return await tmux.capture_pane(session, escapes=True)

    def register_rpc_handler(self, method: str, handler: RpcHandler) -> None:
        self._rpc_handlers[method] = handler

    def register_default_rpc_handlers(self) -> None:
        """Register all built-in RPC handlers, grouped by namespace under handlers/."""
        from murder.app.service.handlers import register_all

        register_all(self)

    async def start(self) -> None:
        from murder.user_config import ensure_user_themes, load_user_config

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
            self.runtime = Runtime(self.config, self.repo_root, user_cfg=user_cfg)
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
        self.register_default_rpc_handlers()

        self.broker = DurableBroker(self.runtime.bus, self.runtime.db)
        for method, handler in self._rpc_handlers.items():
            self.broker.register_rpc_handler(method, handler)

        self.orchestrator = Orchestrator(self.runtime)
        # Route malformed-artifact parse errors back to the owning agent now
        # that the orchestrator (which delivers `agent.message`) exists.
        if self.runtime._sync is not None:
            orch = self.orchestrator

            async def _send_parse_error(agent_id: str, message: str) -> None:
                await orch.send_agent_message(
                    agent_id, message, None, spawn_if_needed=False
                )

            self.runtime._sync.set_parse_error_notifier(_send_parse_error)

        self.socket_server = SocketBusServer(
            self.broker,
            run_id=self.runtime.run_id,
            socket_path=self.socket_path,
            tmux_frame_capture=self._capture_tmux_frame,
        )
        await self.socket_server.start()

        # Startup recovery: reattach handlers to crows whose tmux session
        # survived a service restart so DONE is consumed and the ticket
        # finishes. Each reattach can block on a harness ready-poll (up to
        # 240s), so this runs as a best-effort background task launched AFTER
        # the socket is open — it must never delay boot — and the reattaches
        # run concurrently rather than sequentially.
        self._reattach_task = asyncio.create_task(
            self._reattach_surviving_crows(), name="crow-reattach"
        )

        if self.tcp_port is not None:
            self.tcp_bound = await self.socket_server.start_tcp_listener(port=self.tcp_port)
            LOGGER.info("TCP bus listener on %s:%d", *self.tcp_bound)

        self.supervisor = await start_supervisor_workers(
            repo_root=self.repo_root,
            runtime=self.runtime,
            orchestrator=self.orchestrator,
            broker=self.broker,
        )
        self._startup_rogue_task = asyncio.create_task(
            self._ensure_startup_rogue_safely(), name="startup-rogue-ensure"
        )
        self._model_discovery_task = asyncio.create_task(
            self._discover_then_write_models_doc(), name="startup-model-discovery"
        )
        self._usage_poll_task = asyncio.create_task(
            run_service_usage_poll_loop(self.broker, self.runtime.db, str(self.runtime.run_id)),
            name="usage-sample-poll",
        )
        self._projection_poll_task = asyncio.create_task(
            self._run_projection_poll_loop(), name="transcript-projection-poll"
        )
        self._transit_poll_task = asyncio.create_task(
            self._run_transit_poll_loop(), name="transit-graph-poll"
        )
        try:
            await self.orchestrator.start_question_listener()
        except Exception as exc:
            LOGGER.error("start_question_listener failed: %s", exc, exc_info=True)
            if self.runtime is not None and self.runtime.bus and self.runtime.run_id:
                from murder.bus import ErrorEvent

                with contextlib.suppress(Exception):
                    await self.runtime.bus.publish(
                        ErrorEvent(
                            run_id=str(self.runtime.run_id),
                            agent_id="system",
                            ticket_id=None,
                            message=f"start_question_listener failed: {exc}",
                            recoverable=False,
                        )
                    )
        session = write_service_session(self.repo_root, self.socket_path)
        self._service_session_name = session.name

    async def _ensure_startup_rogue_safely(self) -> None:
        """Best-effort: spawn the user's configured Startup Rogue on boot.

        Idempotent (the orchestrator reuses a live one); never fatal to startup —
        a spawn failure is logged and swallowed so the daemon still comes up.
        """
        try:
            await self.orchestrator.ensure_startup_rogue()
        except Exception:
            LOGGER.error("ensure_startup_rogue failed", exc_info=True)

    async def _discover_then_write_models_doc(self) -> None:
        """Discover harness models, persist to DB, then write ``HARNESSES_AND_MODELS.md``.

        Chained (not two parallel tasks) so the startup doc reflects the
        *discovered* model lists rather than racing discovery and capturing the
        classvar fallback.  Discovery fires exactly once; results are written
        to both the in-process cache and the SQLite DB.
        """
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
                            LOGGER.debug(
                                "projection tick failed for %s", agent.id, exc_info=True
                            )
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
                                    f"{agent.id}|{live_state}|{queued}".encode("utf-8")
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
                    LOGGER.info(
                        "reattached crow handler for %s (session %s)", tid, crow_session
                    )
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

    async def _run_transit_poll_loop(self) -> None:
        """Service-owned ticker that republishes the Transit graph key when any
        watched branch HEAD moves. Git changes have no UI write to hang an
        invalidation off, so this lightweight poll computes the cheap
        ``transit_fingerprint`` each tick and publishes a key-only
        ``Entity.TRANSIT`` snapshot only when it changes (the client refetches
        the full graph via ``state.transit_snapshot``). Modeled on
        ``_run_projection_poll_loop`` so it stays compatible with the conftest
        noop-sleep patch (no busy-spin)."""
        from murder.bus import Entity
        from murder.state.storage.git_transit import transit_fingerprint

        last_fingerprint: str | None = None
        while True:
            runtime = self.runtime
            if runtime is not None:
                try:
                    fingerprint = await asyncio.to_thread(
                        transit_fingerprint, self.repo_root
                    )
                except Exception:
                    fingerprint = last_fingerprint
                    LOGGER.debug(
                        "transit fingerprint tick failed for repo %s"
                        " (keeping last fingerprint=%r)",
                        self.repo_root,
                        last_fingerprint,
                        exc_info=True,
                    )
                if fingerprint != last_fingerprint:
                    last_fingerprint = fingerprint
                    try:
                        await runtime.publish_snapshot(Entity.TRANSIT, "*")
                    except Exception:
                        LOGGER.debug(
                            "transit snapshot publish failed for %s (fingerprint=%r)",
                            Entity.TRANSIT,
                            fingerprint,
                            exc_info=True,
                        )
            await asyncio.sleep(TRANSIT_POLL_INTERVAL_S)

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

        if self._model_discovery_task is not None:
            self._model_discovery_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._model_discovery_task
            self._model_discovery_task = None

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
        self.broker = None
        self.orchestrator = None

    async def __aenter__(self) -> ServiceHost:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        await self.stop()


__all__ = ["ServiceHost"]
