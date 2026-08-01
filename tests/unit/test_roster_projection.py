"""Roster's feature-owned projection boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from murder.app.service.projection_registry import ProjectionProviderRegistry
from murder.facts.log import replay_projection_inputs
from murder.roster import RosterService, register_projection_provider
from tests.support.database import open_test_repo_db


class _NoopBus:
    async def publish(self, event: Any) -> None:
        pass


def _service(tmp_path: Path) -> tuple[RosterService, Any]:
    path = tmp_path / "murder.db"
    db = open_test_repo_db(path)
    return RosterService(db), db


def test_agent_write_and_roster_invalidation_share_one_transaction(tmp_path: Path) -> None:
    service, conn = _service(tmp_path)
    conn.conn.execute(
        """
        CREATE TRIGGER reject_roster_input
        BEFORE INSERT ON projection_inputs
        WHEN NEW.projection = 'roster'
        BEGIN SELECT RAISE(ABORT, 'reject roster input'); END
        """
    )

    with pytest.raises(Exception, match="reject roster input"):
        service.sync_agent(
            conn,
            agent_id="crow-1",
            role="crow",
            ticket_id=None,
            session=None,
            harness=None,
            model=None,
            status="running",
            start_commit=None,
            worktree_path=None,
            pid=None,
        )

    assert conn.conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0] == 0


def test_registered_roster_provider_builds_the_feature_snapshot(tmp_path: Path) -> None:
    service, conn = _service(tmp_path)
    service.sync_agent(
        conn,
        agent_id="crow-1",
        role="crow",
        ticket_id=None,
        session=None,
        harness=None,
        model=None,
        status="running",
        start_commit=None,
        worktree_path=None,
        pid=None,
    )
    registry = ProjectionProviderRegistry()
    register_projection_provider(registry, service, conn)

    snapshot = registry.snapshot("roster")
    inputs = replay_projection_inputs(conn, projection="roster")

    assert snapshot["sessions"][0]["agent_id"] == "crow-1"
    assert len(inputs) == 1
