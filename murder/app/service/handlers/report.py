"""``report.*`` application handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from murder.app.protocol.lifecycle import ReportCreateParams
from murder.app.protocol.requests import CommandName
from murder.state.persistence import reports as reports_db
from murder.work.reports import ensure_report

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost


def register(host: ServiceHost) -> None:
    async def _report_create(body: dict[str, Any]) -> dict[str, Any]:
        params = ReportCreateParams.model_validate(body)
        db = host.db
        if db is None:
            raise RuntimeError("database unavailable")
        existing = reports_db.get_report(db, params.name)
        if existing is not None:
            raise ValueError(f"report already exists: {params.name}")
        row = ensure_report(db, host.repo_root, params.name, body=params.body)
        return {
            "handled": True,
            "ok": True,
            "name": str(row.get("name", params.name)),
        }

    host.register_application_command(CommandName.REPORT_CREATE, _report_create)
