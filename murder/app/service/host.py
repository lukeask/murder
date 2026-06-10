"""ServiceHost — backend composition root for the murder service process."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from murder.app.service.bootstrap import start_supervisor_workers
from murder.app.service.client_api import dto_to_wire
from murder.app.service.read_model import ServiceReadModel
from murder.app.service.runtime import Runtime
from murder.app.service.supervisor import Supervisor
from murder.bus.broker import DurableBroker
from murder.bus.protocol import CommandEvent
from murder.bus.transport_socket import SocketBusServer, default_socket_path
from murder.config import Config
from murder.llm.harnesses.harnesses_doc import write_harnesses_doc
from murder.llm.harnesses.model_cache import refresh_and_persist_harness_models
from murder.runtime.orchestration.orchestrator import Orchestrator
from murder.state.persistence.commands import get_command_status
from murder.state.storage.paths import db_path
from murder.state.storage.service_registry import (
    project_session_name,
    remove_service_session,
    write_service_session,
)
from murder.usage_sample_command import run_service_usage_poll_loop

LOGGER = logging.getLogger(__name__)

RpcHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]]


@dataclass
class ServiceHost:
    """Wires runtime, bus broker, socket server, orchestrator, and supervisor."""

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
    _model_discovery_task: asyncio.Task[None] | None = field(default=None, repr=False)
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
        """Built-in health and command RPCs used by CLI/TUI clients."""
        self.register_rpc_handler(
            "health.ping",
            lambda _body: {
                "ok": True,
                "run_id": self.runtime.run_id if self.runtime else None,
                "pid": os.getpid(),
            },
        )

        async def _command_submit(body: dict[str, Any]) -> dict[str, Any]:
            if self.broker is None or self.runtime is None or self.runtime.run_id is None:
                raise RuntimeError("service not started")
            target_worker = str(body.get("target_worker", "")).strip()
            kind = str(body.get("kind", "")).strip()
            payload = body.get("payload")
            if not target_worker or not kind:
                raise ValueError("command.submit requires target_worker and kind")
            if payload is None:
                payload = {}
            if not isinstance(payload, dict):
                raise ValueError("command.submit payload must be an object")
            command = CommandEvent(
                run_id=str(self.runtime.run_id),
                agent_id=str(body.get("agent_id") or "rpc-client"),
                target_worker=target_worker,
                kind=kind,
                payload=payload,
                correlation_id=str(body.get("correlation_id") or f"rpc-{os.getpid()}"),
                idempotency_key=str(body.get("idempotency_key") or os.urandom(16).hex()),
            )
            await self.broker.publish(command)
            return {"ok": True, "command_id": str(command.id)}

        def _command_status(body: dict[str, Any]) -> dict[str, Any]:
            rt = self.runtime
            if rt is None or rt.db is None:
                return {"ok": False, "error": "runtime_db_unavailable"}
            command_id = str(body.get("command_id", "")).strip()
            if not command_id:
                raise ValueError("command.status requires command_id")
            row = get_command_status(rt.db, command_id)
            if row is None:
                return {"ok": False, "error": "not_found", "command_id": command_id}
            return {
                "ok": True,
                "command_id": command_id,
                "status": row["status"],
                "result_json": row["result_json"],
                "last_error": row["last_error"],
                "updated_at": row["updated_at"],
            }

        self.register_rpc_handler("command.submit", _command_submit)
        self.register_rpc_handler("command.status", _command_status)

        def _read_model() -> ServiceReadModel:
            if self.read_model is None:
                raise RuntimeError("read model unavailable")
            return self.read_model

        def _value(value: Any) -> dict[str, Any]:
            return {"ok": True, "value": dto_to_wire(value)}

        def _state_schedule_snapshot(_body: dict[str, Any]) -> dict[str, Any]:
            return _value(_read_model().get_schedule_snapshot())

        def _state_crow_snapshot(_body: dict[str, Any]) -> dict[str, Any]:
            return _value(_read_model().get_crow_snapshot())

        def _state_conversations_snapshot(_body: dict[str, Any]) -> dict[str, Any]:
            return _value(_read_model().get_conversations_snapshot())

        def _state_plans_snapshot(_body: dict[str, Any]) -> dict[str, Any]:
            return _value(_read_model().get_plans_snapshot())

        def _state_notes_snapshot(_body: dict[str, Any]) -> dict[str, Any]:
            return _value(_read_model().get_notes_snapshot())

        def _state_reports_snapshot(_body: dict[str, Any]) -> dict[str, Any]:
            return _value(_read_model().get_reports_snapshot())

        def _state_ticket_detail(body: dict[str, Any]) -> dict[str, Any]:
            ticket_id = str(body.get("ticket_id", "")).strip()
            if not ticket_id:
                raise ValueError("state.ticket_detail requires ticket_id")
            try:
                return _value(_read_model().get_ticket_detail(ticket_id))
            except KeyError:
                return _value(None)

        def _state_plan_display(body: dict[str, Any]) -> dict[str, Any]:
            name = str(body.get("name", "")).strip()
            if not name:
                raise ValueError("state.plan_display requires name")
            return _value(_read_model().get_plan_display(name))

        def _state_note_display(body: dict[str, Any]) -> dict[str, Any]:
            name = str(body.get("name", "")).strip()
            if not name:
                raise ValueError("state.note_display requires name")
            return _value(_read_model().get_note_display(name))

        def _state_report_display(body: dict[str, Any]) -> dict[str, Any]:
            name = str(body.get("name", "")).strip()
            if not name:
                raise ValueError("state.report_display requires name")
            return _value(_read_model().get_report_display(name))

        def _state_harness_models_snapshot(_body: dict[str, Any]) -> dict[str, Any]:
            return _value(_read_model().get_harness_models_snapshot())

        self.register_rpc_handler("state.schedule_snapshot", _state_schedule_snapshot)
        self.register_rpc_handler("state.crow_snapshot", _state_crow_snapshot)
        self.register_rpc_handler("state.conversations_snapshot", _state_conversations_snapshot)
        self.register_rpc_handler("state.plans_snapshot", _state_plans_snapshot)
        self.register_rpc_handler("state.notes_snapshot", _state_notes_snapshot)
        self.register_rpc_handler("state.reports_snapshot", _state_reports_snapshot)
        self.register_rpc_handler("state.ticket_detail", _state_ticket_detail)
        self.register_rpc_handler("state.plan_display", _state_plan_display)
        self.register_rpc_handler("state.note_display", _state_note_display)
        self.register_rpc_handler("state.report_display", _state_report_display)
        self.register_rpc_handler(
            "state.harness_models_snapshot",
            _state_harness_models_snapshot,
        )

        def _orchestrator() -> Orchestrator:
            if self.orchestrator is None:
                raise RuntimeError("orchestrator unavailable")
            return self.orchestrator

        def _ticket_next_id(_body: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True, "ticket_id": _orchestrator().next_ticket_id()}

        def _ticket_exists(body: dict[str, Any]) -> dict[str, Any]:
            handle = str(body.get("handle", "")).strip()
            if not handle:
                raise ValueError("ticket.exists requires handle")
            return {"ok": True, "exists": _orchestrator().ticket_exists(handle)}

        async def _ticket_save_body(body: dict[str, Any]) -> dict[str, Any]:
            ticket_id = str(body.get("ticket_id", "")).strip()
            if not ticket_id:
                raise ValueError("ticket.save_body requires ticket_id")
            md = body.get("body")
            if not isinstance(md, str):
                raise ValueError("ticket.save_body requires body string")
            return await _orchestrator().save_ticket_body(ticket_id, md)

        async def _ticket_schedule(body: dict[str, Any]) -> dict[str, Any]:
            ticket_id = str(body.get("ticket_id", "")).strip()
            if not ticket_id:
                raise ValueError("ticket.schedule requires ticket_id")
            duration = str(body.get("duration", ""))
            return await _orchestrator().schedule_ticket(ticket_id, duration)

        async def _plan_create(body: dict[str, Any]) -> dict[str, Any]:
            plan_name = str(body.get("plan_name", "")).strip()
            auto_name = bool(body.get("auto_name", False))
            if not plan_name and not auto_name:
                raise ValueError("plan.create requires plan_name or auto_name")
            message = str(body.get("message", ""))
            plan_body = body.get("body")
            return await _orchestrator().create_plan(
                plan_name,
                message,
                body=plan_body if isinstance(plan_body, str) else None,
                auto_name=auto_name,
            )

        def _image_upload(body: dict[str, Any]) -> dict[str, Any]:
            # F9: store a pasted clipboard image under .murder/images and return
            # the stored path. Bytes ride base64 over JSON-RPC.
            #
            # The client now mints the filename ``stem`` at paste time and passes
            # it as ``name`` (so the label<->file binding is known instantly,
            # client-side). The server no longer mints it. But the service NEVER
            # trusts a path from the wire: both ``name`` and ``ext`` are
            # sanitized to the basename charset before being joined into the
            # path, so a traversal attempt (``../../etc/foo``) collapses to a
            # harmless basename. This guard is unconditional (the bus is a local
            # UDS with only our own TUI as client, but the invariant holds
            # regardless).
            import base64
            import re

            from murder.state.storage.paths import murder_dir as _murder_dir

            data_b64 = body.get("bytes")
            if not isinstance(data_b64, str) or not data_b64:
                raise ValueError("image.upload requires base64 bytes")
            try:
                data = base64.b64decode(data_b64, validate=True)
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"invalid base64: {exc}"}

            def _sanitize(value: str) -> str:
                return re.sub(r"[^a-zA-Z0-9._-]", "", value)

            stem = _sanitize(str(body.get("name") or ""))
            if not stem:
                return {"ok": False, "error": "image.upload requires a non-empty name"}
            ext = _sanitize(str(body.get("ext") or "png").lstrip(".")) or "png"

            images_dir = _murder_dir(self.repo_root) / "images"
            images_dir.mkdir(parents=True, exist_ok=True)
            fpath = images_dir / f"{stem}.{ext}"
            fpath.write_bytes(data)
            return {"ok": True, "path": str(fpath)}

        def _tui_prefs_file() -> Path:
            from murder.state.storage.paths import tui_prefs_path as _tui_prefs_path

            return _tui_prefs_path(self.repo_root)

        def _tui_load_favorites(_body: dict[str, Any]) -> dict[str, Any]:
            import json

            path = _tui_prefs_file()
            if not path.exists():
                return {"ok": True, "favorites": []}
            try:
                data = json.loads(path.read_text())
                favorites = data.get("favorites", [])
                if not isinstance(favorites, list):
                    favorites = []
            except Exception:  # noqa: BLE001
                favorites = []
            return {"ok": True, "favorites": [str(item) for item in favorites]}

        def _tui_save_favorites(body: dict[str, Any]) -> dict[str, Any]:
            import json

            favorites = body.get("favorites")
            if not isinstance(favorites, list):
                raise ValueError("tui.save_favorites requires favorites list")
            ids = sorted({str(item) for item in favorites})
            path = _tui_prefs_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"favorites": ids}))
            tmp.replace(path)
            return {"ok": True, "favorites": ids}

        def _settings_payload(tui: Any) -> dict[str, Any]:
            return {
                "theme": tui.theme,
                "modifier": tui.modifier,
                "key_overrides": dict(tui.key_overrides),
            }

        def _settings_get(_body: dict[str, Any]) -> dict[str, Any]:
            from murder.user_config import load_user_config

            cfg = load_user_config()
            return {"ok": True, "settings": _settings_payload(cfg.tui)}

        def _settings_update(body: dict[str, Any]) -> dict[str, Any]:
            # Partial merge: load the persisted user config, overlay only the provided tui keys,
            # re-validate via pydantic (modifier/theme), and persist. We call load/save directly
            # rather than SettingsService.save_global to avoid its model-discovery side effects.
            from murder.user_config import (
                TuiUserConfig,
                load_user_config,
                save_user_config,
            )

            partial = body.get("settings")
            if not isinstance(partial, dict):
                raise ValueError("settings.update requires a settings object")

            cfg = load_user_config()
            merged: dict[str, Any] = _settings_payload(cfg.tui)
            for key in ("theme", "modifier", "key_overrides"):
                if key in partial:
                    merged[key] = partial[key]
            # Re-validate the merged tui block; an invalid modifier/theme/key_overrides raises here.
            cfg.tui = TuiUserConfig.model_validate(merged)
            save_user_config(cfg)
            return {"ok": True, "settings": _settings_payload(cfg.tui)}

        def _worktree_list(_body: dict[str, Any]) -> dict[str, Any]:
            from murder.state.storage.worktrees import list_murder_worktrees_sync

            entries = list_murder_worktrees_sync(self.repo_root)
            return {
                "ok": True,
                "entries": [
                    {
                        "path": str(entry.path),
                        "branch": entry.branch,
                        "is_main": entry.is_main,
                    }
                    for entry in entries
                ],
            }

        self.register_rpc_handler("ticket.next_id", _ticket_next_id)
        self.register_rpc_handler("ticket.exists", _ticket_exists)
        self.register_rpc_handler("ticket.save_body", _ticket_save_body)
        self.register_rpc_handler("ticket.schedule", _ticket_schedule)
        self.register_rpc_handler("plan.create", _plan_create)
        self.register_rpc_handler("image.upload", _image_upload)
        self.register_rpc_handler("tui.load_favorites", _tui_load_favorites)
        self.register_rpc_handler("tui.save_favorites", _tui_save_favorites)
        self.register_rpc_handler("settings.get", _settings_get)
        self.register_rpc_handler("settings.update", _settings_update)
        self.register_rpc_handler("worktree.list", _worktree_list)

    async def start(self) -> None:
        self.runtime = Runtime(self.config, self.repo_root)
        await self.runtime.start()
        if self.runtime.db is None or self.runtime.bus is None or self.runtime.run_id is None:
            raise RuntimeError("runtime failed to initialize db/bus/run_id")
        self.read_model = ServiceReadModel(db_path(self.repo_root))

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
        if self.tcp_port is not None:
            self.tcp_bound = await self.socket_server.start_tcp_listener(port=self.tcp_port)
            LOGGER.info("TCP bus listener on %s:%d", *self.tcp_bound)

        self.supervisor = await start_supervisor_workers(
            repo_root=self.repo_root,
            runtime=self.runtime,
            orchestrator=self.orchestrator,
            broker=self.broker,
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
        with contextlib.suppress(Exception):
            await self.orchestrator.start_question_listener()
        session = write_service_session(self.repo_root, self.socket_path)
        self._service_session_name = session.name

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

        while True:
            runtime = self.runtime
            if runtime is not None:
                for agent in runtime.agents.all_agents():
                    if not isinstance(agent, HarnessBackedAgent):
                        continue
                    try:
                        await agent.project_once()
                    except tmux.TmuxError:
                        pass
                    except Exception:
                        LOGGER.debug("projection tick failed for %s", agent.id, exc_info=True)
            await asyncio.sleep(PROJECTION_INTERVAL_S)

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

        if self._model_discovery_task is not None:
            self._model_discovery_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._model_discovery_task
            self._model_discovery_task = None

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
            with contextlib.suppress(Exception):
                self.runtime._external_stop.clear()
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
