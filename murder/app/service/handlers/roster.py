"""Roster application query registration."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from murder.app.protocol.operations import RosterGetParams
from murder.app.protocol.requests import QueryName

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost


def register(host: ServiceHost) -> None:
    def _get(body: dict[str, object]) -> dict[str, object]:
        RosterGetParams.model_validate(body)
        runtime = host.runtime
        if runtime is None:
            raise RuntimeError("service not started")
        return runtime.roster.get()

    # The roster is application-only now: no compatibility RPC target remains.
    host.register_application_query(
        QueryName.ROSTER_GET,
        lambda body: asyncio.to_thread(_get, body),
    )


__all__ = ["register"]
