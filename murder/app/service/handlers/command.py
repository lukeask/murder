"""``command.*`` RPC handlers."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from murder.bus.protocol import CommandEvent
from murder.state.persistence.commands import get_command_status

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost


def register(host: ServiceHost) -> None:
    async def _command_submit(body: dict[str, Any]) -> dict[str, Any]:
        if host.broker is None or host.runtime is None or host.runtime.run_id is None:
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
            run_id=str(host.runtime.run_id),
            agent_id=str(body.get("agent_id") or "rpc-client"),
            target_worker=target_worker,
            kind=kind,
            payload=payload,
            correlation_id=str(body.get("correlation_id") or f"rpc-{os.getpid()}"),
            idempotency_key=str(body.get("idempotency_key") or os.urandom(16).hex()),
        )
        await host.broker.publish(command)
        return {"ok": True, "command_id": str(command.id)}

    def _command_status(body: dict[str, Any]) -> dict[str, Any]:
        rt = host.runtime
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

    host.register_rpc_handler("command.submit", _command_submit)
    host.register_rpc_handler("command.status", _command_status)
