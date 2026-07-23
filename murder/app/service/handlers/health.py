"""``health.*`` RPC handlers."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from murder.app.protocol.requests import QueryName

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost


def register(host: ServiceHost) -> None:
    def _health(_body: dict[str, object]) -> dict[str, object]:
        return {
            "ok": True,
            "run_id": host.runtime.run_id if host.runtime else None,
            "pid": os.getpid(),
        }

    host.register_application_query(QueryName.HEALTH_GET, _health)
