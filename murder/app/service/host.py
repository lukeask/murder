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

# Cadence for the Transit git-graph fingerprint poll (branch HEAD movement).
TRANSIT_POLL_INTERVAL_S = 4.0

RpcHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]]


def _deep_merge_settings(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *over* into *base*, returning a new dict.

    Nested dicts merge key-by-key; everything else (scalars, lists) is replaced.
    Used to apply a partial `llm` patch onto the stored block.
    """
    out = dict(base)
    for k, v in over.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge_settings(out[k], v)
        else:
            out[k] = v
    return out


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
    _transit_poll_task: asyncio.Task[None] | None = field(default=None, repr=False)
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

        def _state_history_snapshot(_body: dict[str, Any]) -> dict[str, Any]:
            return _value(_read_model().get_history_snapshot())

        def _state_transit_snapshot(_body: dict[str, Any]) -> dict[str, Any]:
            return _value(_read_model().get_transit_snapshot())

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
        self.register_rpc_handler("state.history_snapshot", _state_history_snapshot)
        self.register_rpc_handler("state.transit_snapshot", _state_transit_snapshot)
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

        def _mask_llm(llm: Any) -> dict[str, Any]:
            # Dump the user llm block, masking every non-empty api_key as "***".
            if llm is None:
                return {}
            data = llm.model_dump(mode="json")
            for provider in (data.get("providers") or {}).values():
                if isinstance(provider, dict) and provider.get("api_key"):
                    provider["api_key"] = "***"
            return data

        def _crow_harnesses_override(cfg: Any) -> list[str] | None:
            # The user-scope default_crow override: harnesses pool, or [harness]
            # if only the scalar is set, else None (no override).
            crow = cfg.default_crow
            if crow is None:
                return None
            if crow.harnesses:
                return list(crow.harnesses)
            if crow.harness is not None:
                return [crow.harness]
            return None

        def _settings_payload(cfg: Any) -> dict[str, Any]:
            import os as _os

            tui = cfg.tui
            collab_override = (
                cfg.collaborator.harness if cfg.collaborator is not None else None
            )
            live_crow = self.config.default_crow
            effective_crow = (
                list(live_crow.harnesses) if live_crow.harnesses else [live_crow.harness]
            )
            return {
                # --- existing tui fields (unchanged) ---
                "theme": tui.theme,
                "modifier": tui.modifier,
                "key_overrides": dict(tui.key_overrides),
                "pane_gap": tui.pane_gap,
                # --- harness overrides + effective values ---
                "collaborator_harness": collab_override,
                "crow_harnesses": _crow_harnesses_override(cfg),
                "effective_collaborator_harness": self.config.collaborator.harness,
                "effective_crow_harnesses": effective_crow,
                # --- llm provider/tier/role config (api keys masked) ---
                "llm": _mask_llm(cfg.llm),
                "llm_env": {
                    "groq": bool(_os.environ.get("GROQ_API_KEY")),
                    "cerebras": bool(_os.environ.get("CEREBRAS_API_KEY")),
                    "openrouter": bool(_os.environ.get("OPENROUTER_API_KEY")),
                },
            }

        def _settings_get(_body: dict[str, Any]) -> dict[str, Any]:
            from murder.user_config import load_user_config

            cfg = load_user_config()
            return {"ok": True, "settings": _settings_payload(cfg)}

        def _settings_update(body: dict[str, Any]) -> dict[str, Any]:
            # Partial merge: load the persisted user config, overlay only the provided keys,
            # re-validate via pydantic, and persist. We call load/save directly rather than
            # SettingsService.save_global to avoid its model-discovery side effects.
            from typing import get_args

            from murder.user_config import (
                TuiUserConfig,
                UserHarnessKind,
                UserHarnessRolePatch,
                UserLlmConfig,
                load_user_config,
                save_user_config,
            )

            partial = body.get("settings")
            if not isinstance(partial, dict):
                raise ValueError("settings.update requires a settings object")

            cfg = load_user_config()

            # --- tui keys (re-validate the merged tui block) ---
            tui_merged: dict[str, Any] = {
                "theme": cfg.tui.theme,
                "modifier": cfg.tui.modifier,
                "key_overrides": dict(cfg.tui.key_overrides),
                "pane_gap": cfg.tui.pane_gap,
            }
            for key in ("theme", "modifier", "key_overrides", "pane_gap"):
                if key in partial:
                    tui_merged[key] = partial[key]
            cfg.tui = TuiUserConfig.model_validate(tui_merged)

            valid_harnesses = set(get_args(UserHarnessKind))

            # --- collaborator_harness override ---
            if "collaborator_harness" in partial:
                value = partial["collaborator_harness"]
                if value is None:
                    if cfg.collaborator is not None:
                        cfg.collaborator.harness = None
                else:
                    if value not in valid_harnesses:
                        raise ValueError(f"invalid collaborator harness: {value!r}")
                    patch = cfg.collaborator or UserHarnessRolePatch()
                    patch.harness = value
                    cfg.collaborator = patch
                    # Apply live so new spawns use it without a daemon restart.
                    self.config.collaborator.harness = value

            # --- crow_harnesses override (single -> harness; multi -> harnesses; null -> clear) ---
            if "crow_harnesses" in partial:
                value = partial["crow_harnesses"]
                if value is None:
                    if cfg.default_crow is not None:
                        cfg.default_crow.harness = None
                        cfg.default_crow.harnesses = None
                else:
                    if not isinstance(value, list) or not value:
                        raise ValueError("crow_harnesses must be a non-empty list or null")
                    for h in value:
                        if h not in valid_harnesses:
                            raise ValueError(f"invalid crow harness: {h!r}")
                    patch = cfg.default_crow or UserHarnessRolePatch()
                    if len(value) == 1:
                        patch.harness = value[0]
                        patch.harnesses = None
                    else:
                        patch.harness = value[0]
                        patch.harnesses = list(value)
                    cfg.default_crow = patch
                    # Apply live so new spawns use it without a daemon restart.
                    self.config.default_crow.harness = value[0]
                    self.config.default_crow.harnesses = (
                        list(value) if len(value) > 1 else None
                    )

            # --- llm block (deep-merge; "***" api_key sentinel = keep stored value) ---
            if "llm" in partial:
                incoming = partial["llm"]
                if not isinstance(incoming, dict):
                    raise ValueError("llm must be an object")
                existing = (
                    cfg.llm.model_dump(mode="json") if cfg.llm is not None else {}
                )
                merged_llm = _deep_merge_settings(existing, incoming)
                # Resolve "***" sentinels: an incoming api_key of "***" means
                # "unchanged" — restore the stored value (empty string clears).
                stored_providers = (existing.get("providers") or {})
                for name, provider in (merged_llm.get("providers") or {}).items():
                    if not isinstance(provider, dict):
                        continue
                    if provider.get("api_key") == "***":
                        stored = stored_providers.get(name) or {}
                        provider["api_key"] = stored.get("api_key")
                cfg.llm = UserLlmConfig.model_validate(merged_llm)

            save_user_config(cfg)
            # NOTE: llm env changes are NOT applied live; they take effect at next
            # daemon start via apply_llm_env in Config.load.
            return {"ok": True, "settings": _settings_payload(cfg)}

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
        from murder.user_config import load_user_config

        try:
            user_cfg = load_user_config()
        except Exception:
            user_cfg = None
        self.runtime = Runtime(self.config, self.repo_root, user_cfg=user_cfg)
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

        # Startup recovery: reattach handlers to crows whose tmux session
        # survived a service restart so DONE is consumed and the ticket finishes.
        report = getattr(self.runtime, "startup_reconcile_report", None)
        if report and report.crows_to_reattach:
            for tid, crow_session in report.crows_to_reattach:
                try:
                    await self.orchestrator.reattach_crow(tid, crow_session)
                    LOGGER.info(
                        "reattached crow handler for %s (session %s)", tid, crow_session
                    )
                except Exception:
                    LOGGER.error("failed to reattach crow for %s", tid, exc_info=True)

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
                        pass
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
            await asyncio.sleep(PROJECTION_INTERVAL_S)

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
                    fingerprint = transit_fingerprint(self.repo_root)
                except Exception:
                    fingerprint = last_fingerprint
                    LOGGER.debug("transit fingerprint tick failed", exc_info=True)
                if fingerprint != last_fingerprint:
                    last_fingerprint = fingerprint
                    try:
                        await runtime.publish_snapshot(Entity.TRANSIT, "*")
                    except Exception:
                        LOGGER.debug("transit snapshot publish failed", exc_info=True)
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
