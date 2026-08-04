"""Roster application query registration."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from murder.app.protocol.operations import RosterGetParams
from murder.app.protocol.requests import QueryName
from murder.app.service.projection_registry import ProjectionProviderRegistry
from murder.roster.service import register_projection_provider

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost


def register(
    host: ServiceHost,
    projections: ProjectionProviderRegistry | None = None,
) -> None:
    def _get(body: dict[str, object]) -> dict[str, object]:
        RosterGetParams.model_validate(body)
        roster = host.roster
        if roster is None:
            raise RuntimeError("service not started")
        return roster.get()

    # The roster is application-only now: no compatibility RPC target remains.
    host.register_application_query(
        QueryName.ROSTER_GET,
        lambda body: asyncio.to_thread(_get, body),
    )
    if projections is not None:
        roster = host.roster
        db = host.db
        if roster is None or db is None:
            raise RuntimeError("service not started")
        register_projection_provider(projections, roster, db)


__all__ = ["register"]
