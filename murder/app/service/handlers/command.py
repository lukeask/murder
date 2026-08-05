"""``command.*`` application handlers."""

from __future__ import annotations

from typing import Any

from murder.app.protocol.reads import CommandGetParams, CommandGetResult
from murder.app.protocol.requests import QueryName
from murder.app.service.application import ApplicationRegistrar
from murder.state.persistence.connection import RepoDb


def register(app: ApplicationRegistrar, db: RepoDb) -> None:
    def _command_status(body: dict[str, Any]) -> dict[str, Any]:
        from murder.state.persistence.commands import get_command_status

        params = CommandGetParams.model_validate(body)
        row = get_command_status(db, params.command_id)
        if row is None:
            return CommandGetResult(
                ok=False, error="not_found", command_id=params.command_id
            ).model_dump(mode="json")
        return CommandGetResult(
            ok=True,
            command_id=params.command_id,
            status=row["status"],
            result_json=row["result_json"],
            last_error=row["last_error"],
            updated_at=row["updated_at"],
        ).model_dump(mode="json")

    app.register_application_query(QueryName.COMMAND_GET, _command_status)
