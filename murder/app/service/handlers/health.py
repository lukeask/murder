"""``health.*`` application handlers."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from murder.app.protocol.reads import EmptyParams, HealthGetResult
from murder.app.protocol.requests import QueryName

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost


def register(host: ServiceHost) -> None:
    def _health(body: dict[str, object]) -> dict[str, object]:
        EmptyParams.model_validate(body or {})
        return HealthGetResult(
            ok=True,
            run_id=host.runtime.run_id if host.runtime else None,
            pid=os.getpid(),
        ).model_dump(mode="json")

    host.register_application_query(QueryName.HEALTH_GET, _health)
