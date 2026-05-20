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

from murder.bus.broker import DurableBroker
from murder.bus.protocol import CommandEvent
from murder.bus.transport_socket import SocketBusServer, default_socket_path
from murder.config import Config
from murder_newstructure.orchestration.orchestrator import Orchestrator
from murder_newstructure.persistence.commands import get_command_status
from murder_newstructure.service.bootstrap import start_supervisor_workers
from murder_newstructure.service.runtime import Runtime
from murder_newstructure.service.settings_service import SettingsService
from murder_newstructure.service.supervisor import Supervisor
from murder.usage_sample_command import run_service_usage_poll_loop

LOGGER = logging.getLogger(__name__)

RpcHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]]


@dataclass
class ServiceHost:
    """Wires runtime, bus broker, socket server, orchestrator, and supervisor."""

    config: Config
    repo_root: Path
    socket_path: Path = field(default_factory=default_socket_path)
    runtime: Runtime | None = None
    broker: DurableBroker | None = None
    orchestrator: Orchestrator | None = None
    supervisor: Supervisor | None = None
    socket_server: SocketBusServer | None = None
    _usage_poll_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _rpc_handlers: dict[str, RpcHandler] = field(default_factory=dict, repr=False)

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

        settings = SettingsService(self.repo_root)

        async def _settings_discover_models(body: dict[str, Any]) -> dict[str, Any]:
            harness = str(body.get("harness", "")).strip()
            if not harness:
                raise ValueError("settings.discover_models requires harness")
            result = await settings.discover_models(harness)
            return {
                "ok": result.ok,
                "message": result.message,
                "models": [{"id": mid, "label": label} for mid, label in result.models],
            }

        self.register_rpc_handler("settings.discover_models", _settings_discover_models)

    async def start(self) -> None:
        self.runtime = Runtime(self.config, self.repo_root)
        await self.runtime.start()
        if self.runtime.db is None or self.runtime.bus is None or self.runtime.run_id is None:
            raise RuntimeError("runtime failed to initialize db/bus/run_id")

        self.register_default_rpc_handlers()

        self.broker = DurableBroker(self.runtime.bus, self.runtime.db)
        for method, handler in self._rpc_handlers.items():
            self.broker.register_rpc_handler(method, handler)

        self.orchestrator = Orchestrator(self.runtime)
        self.socket_server = SocketBusServer(
            self.broker,
            run_id=self.runtime.run_id,
            socket_path=self.socket_path,
        )
        await self.socket_server.start()

        self.supervisor = await start_supervisor_workers(
            repo_root=self.repo_root,
            runtime=self.runtime,
            orchestrator=self.orchestrator,
            broker=self.broker,
        )
        self._usage_poll_task = asyncio.create_task(
            run_service_usage_poll_loop(self.broker, self.runtime.db, str(self.runtime.run_id)),
            name="usage-sample-poll",
        )
        if os.environ.get("OPENROUTER_API_KEY"):
            with contextlib.suppress(Exception):
                await self.orchestrator.ensure_sentinel()

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

        if self.supervisor is not None:
            await self.supervisor.stop_all()
            self.supervisor = None

        if self.socket_server is not None:
            with contextlib.suppress(FileNotFoundError, OSError):
                await self.socket_server.stop()
            self.socket_server = None

        if self.runtime is not None:
            with contextlib.suppress(Exception):
                self.runtime._external_stop.clear()
            await self.runtime.stop()
            self.runtime = None

        self.broker = None
        self.orchestrator = None

    async def __aenter__(self) -> ServiceHost:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        await self.stop()


__all__ = ["ServiceHost"]
