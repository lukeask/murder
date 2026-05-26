"""Crows wall projection — cookbook then edge cases."""

from __future__ import annotations

from datetime import datetime, timezone

from murder.service.client_api import CrowSessionSummary, CrowSnapshot
from murder.tui.crows_view import entries_from_snapshot


def _session(**kwargs: object) -> CrowSessionSummary:
    defaults = dict(
        agent_id="crow-t001",
        role="crow",
        ticket_id="t001",
        ticket_title="Fix thing",
        status="running",
        session_name="murder_demo_crow_t001",
        harness="cursor",
        last_seen=None,
        started_at=None,
        ticket_status="in_progress",
    )
    defaults.update(kwargs)
    return CrowSessionSummary(**defaults)  # type: ignore[arg-type]


def test_entries_from_snapshot_includes_running_crow() -> None:
    snap = CrowSnapshot(
        sessions=(_session(),),
        as_of=datetime.now(timezone.utc),
        invalidation_key="k",
    )
    entries = entries_from_snapshot(snap)
    assert len(entries) == 1
    assert entries[0].agent_id == "crow-t001"


def test_entries_from_snapshot_skips_handlers() -> None:
    snap = CrowSnapshot(
        sessions=(
            _session(agent_id="crow_handler-t001", role="crow_handler", harness=""),
            _session(agent_id="planning_handler-plan", role="planning_handler", harness=""),
            _session(),
        ),
        as_of=datetime.now(timezone.utc),
        invalidation_key="k",
    )
    entries = entries_from_snapshot(snap)
    assert [e.agent_id for e in entries] == ["crow-t001"]
