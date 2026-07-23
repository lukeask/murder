"""Roster application service and feature projection provider."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from murder.app.protocol.read_models import dto_to_wire
from murder.app.protocol.subscriptions import ProjectionTopic
from murder.app.service.projection_registry import ProjectionProviderRegistry
from murder.state.persistence.schema import get_db

from .repository import RosterRepository


class RosterService:
    """Small typed use-case API for roster reads and runtime-owned writes."""

    def __init__(self, db_path: Path, *, repository: RosterRepository | None = None) -> None:
        self._db_path = Path(db_path)
        self._repository = repository or RosterRepository()

    def get(self) -> dict[str, object]:
        """Return the roster application query result using a fresh read connection."""

        with closing(get_db(self._db_path)) as conn:
            return dto_to_wire(self._repository.snapshot(conn))

    def sync_agent(self, conn: sqlite3.Connection, **agent: object) -> None:
        self._repository.sync_agent(conn, **agent)  # type: ignore[arg-type]

    def set_agent_status(self, conn: sqlite3.Connection, agent_id: str, status: str) -> None:
        self._repository.set_agent_status(conn, agent_id=agent_id, status=status)

    def heartbeat_agent(
        self, conn: sqlite3.Connection, agent_id: str, *, invalidate: bool
    ) -> None:
        self._repository.heartbeat_agent(conn, agent_id=agent_id, invalidate=invalidate)

    def projection_snapshot(self, conn: sqlite3.Connection) -> dict[str, object]:
        return dto_to_wire(self._repository.snapshot(conn))


def register_projection_provider(
    registry: ProjectionProviderRegistry,
    service: RosterService,
    conn: sqlite3.Connection,
) -> None:
    """Register roster's snapshot builder without leaking its SQL to the broker."""

    registry.register(ProjectionTopic.ROSTER, lambda: service.projection_snapshot(conn))


__all__ = ["RosterService", "register_projection_provider"]
