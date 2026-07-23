"""``command.*`` RPC handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from murder.app.protocol.requests import QueryName

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost


def register(host: ServiceHost) -> None:
    def _command_status(body: dict[str, Any]) -> dict[str, Any]:
        from murder.state.persistence.commands import get_command_status

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

    host.register_application_query(QueryName.COMMAND_GET, _command_status)
