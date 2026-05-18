from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from murder.orchestrator import Orchestrator
from murder.runtime import Runtime


def _runtime(memdb, tmp_path: Path) -> Runtime:
    cfg = SimpleNamespace(
        project=SimpleNamespace(name="test"),
        runtime=SimpleNamespace(session_name_template="murder_{project}_{role}{suffix}"),
        collaborator=SimpleNamespace(
            startup_model=None,
            harness="claude_code",
            startup_prompt_template="collaborator.md",
        ),
    )
    rt = Runtime(cfg, tmp_path)  # type: ignore[arg-type]
    rt.db = memdb
    return rt


def _insert_ticket(memdb, ticket_id: str, status: str) -> None:
    now = "2026-05-15T12:00:00"
    memdb.execute(
        """
        INSERT INTO tickets (id, title, wave, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (ticket_id, f"Ticket {ticket_id}", 1, status, now, now),
    )


@pytest.mark.asyncio
async def test_orchestrator_fail_persists_last_error_and_retry_clears_it(
    memdb, tmp_path: Path
) -> None:
    _insert_ticket(memdb, "t900", "in_progress")
    rt = _runtime(memdb, tmp_path)
    orch = Orchestrator(rt)

    await orch._fail_ticket("t900", "crow bootstrap timed out")

    failed = memdb.execute("SELECT status, last_error FROM tickets WHERE id = 't900'").fetchone()
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["last_error"] == "crow bootstrap timed out"

    await orch.retry_failed_ticket("t900")

    retried = memdb.execute("SELECT status, last_error FROM tickets WHERE id = 't900'").fetchone()
    assert retried is not None
    assert retried["status"] == "planned"
    assert retried["last_error"] is None
