"""Roster application query registration."""

from __future__ import annotations

import asyncio

from murder.app.protocol.operations import RosterGetParams
from murder.app.protocol.requests import QueryName
from murder.app.service.application import ApplicationRegistrar
from murder.app.service.projection_registry import ProjectionProviderRegistry
from murder.roster.service import RosterService, register_projection_provider
from murder.state.persistence.connection import RepoDb


def register(
    app: ApplicationRegistrar,
    projections: ProjectionProviderRegistry,
    *,
    roster: RosterService,
    db: RepoDb,
) -> None:
    def _get(body: dict[str, object]) -> dict[str, object]:
        RosterGetParams.model_validate(body)
        return roster.get()

    # The roster is application-only now: no compatibility RPC target remains.
    app.register_application_query(
        QueryName.ROSTER_GET,
        lambda body: asyncio.to_thread(_get, body),
    )
    register_projection_provider(projections, roster, db)


__all__ = ["register"]
