"""``report.*`` application handlers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from murder.app.protocol.lifecycle import ReportCreateParams
from murder.app.protocol.requests import CommandName
from murder.app.service.application import ApplicationRegistrar
from murder.state.persistence import reports as reports_db
from murder.state.persistence.connection import RepoDb
from murder.work.reports import ensure_report


def register(app: ApplicationRegistrar, *, db: RepoDb, repo_root: Path) -> None:
    async def _report_create(body: dict[str, Any]) -> dict[str, Any]:
        params = ReportCreateParams.model_validate(body)
        existing = reports_db.get_report(db, params.name)
        if existing is not None:
            raise ValueError(f"report already exists: {params.name}")
        row = ensure_report(db, repo_root, params.name, body=params.body)
        return {
            "handled": True,
            "ok": True,
            "name": str(row.get("name", params.name)),
        }

    app.register_application_command(CommandName.REPORT_CREATE, _report_create)
