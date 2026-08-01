"""Roster application service and feature projection provider."""

from __future__ import annotations

from murder.app.protocol.read_models import dto_to_wire
from murder.app.protocol.subscriptions import ProjectionTopic
from murder.app.service.projection_registry import ProjectionProviderRegistry
from murder.state.persistence.connection import RepoDb

from .repository import RosterRepository


class RosterService:
    """Small typed use-case API for roster reads and runtime-owned writes."""

    def __init__(self, db: RepoDb, *, repository: RosterRepository | None = None) -> None:
        self._db = db
        self._repository = repository or RosterRepository()

    def get(self) -> dict[str, object]:
        """Return the roster application query result for this repository."""
        return dto_to_wire(self._repository.snapshot(self._db))

    def sync_agent(self, db: RepoDb, **agent: object) -> None:
        self._repository.sync_agent(db, **agent)  # type: ignore[arg-type]

    def set_agent_status(self, db: RepoDb, agent_id: str, status: str) -> None:
        self._repository.set_agent_status(db, agent_id=agent_id, status=status)

    def heartbeat_agent(
        self, db: RepoDb, agent_id: str, *, invalidate: bool
    ) -> None:
        self._repository.heartbeat_agent(db, agent_id=agent_id, invalidate=invalidate)

    def projection_snapshot(self, db: RepoDb) -> dict[str, object]:
        return dto_to_wire(self._repository.snapshot(db))


def register_projection_provider(
    registry: ProjectionProviderRegistry,
    service: RosterService,
    db: RepoDb,
) -> None:
    """Register the roster snapshot builder without exposing SQL to the broker."""

    registry.register(ProjectionTopic.ROSTER, lambda: service.projection_snapshot(db))


__all__ = ["RosterService", "register_projection_provider"]
