"""``health.*`` RPC handlers."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost


def register(host: ServiceHost) -> None:
    host.register_rpc_handler(
        "health.ping",
        lambda _body: {
            "ok": True,
            "run_id": host.runtime.run_id if host.runtime else None,
            "pid": os.getpid(),
        },
    )
