"""``health.*`` application handlers."""

from __future__ import annotations

import os

from murder.app.protocol.reads import EmptyParams, HealthGetResult
from murder.app.protocol.requests import QueryName
from murder.app.service.application import ApplicationRegistrar


def register(app: ApplicationRegistrar, *, run_id: str) -> None:
    def _health(body: dict[str, object]) -> dict[str, object]:
        EmptyParams.model_validate(body or {})
        return HealthGetResult(
            ok=True,
            run_id=run_id,
            pid=os.getpid(),
        ).model_dump(mode="json")

    app.register_application_query(QueryName.HEALTH_GET, _health)
