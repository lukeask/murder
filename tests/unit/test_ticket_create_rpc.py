"""C14 / V-list closure — ticket.quick_create, ticket.next_id, ticket.exists.

These pin the new server-side ticket authority the TUI used to bypass with a
direct ``.murder/tickets/<id>.md`` write (V1) and on-disk globbing (V4/V5).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from murder.bus.protocol import CommandEvent
from murder.config import (
    Config,
    CrowHandlerConfig,
    HarnessRoleConfig,
    ProjectConfig,
)
from murder.runtime.orchestration.orchestrator import Orchestrator
from murder.runtime.workers.base import WorkerCtx
from murder.runtime.workers.orchestrator_worker import OrchestratorCommandWorker
from murder.state.persistence.schema import get_db, init_db
from murder.state.storage.paths import ticket_md


@dataclass
class _Runtime:
    repo_root: Path
    config: Config
    db: object
    event_sink: object | None = None
    bus: object | None = None
    run_id: str | None = None


def _orch(repo_root: Path) -> Orchestrator:
    conn = get_db(repo_root / ".murder" / "murder.db")
    init_db(conn)
    config = Config(
        project=ProjectConfig(name="repo"),
        collaborator=HarnessRoleConfig(harness="codex"),
        default_crow=HarnessRoleConfig(harness="codex"),
        crow_handler=CrowHandlerConfig(model="test-model"),
    )
    rt = _Runtime(repo_root=repo_root, config=config, db=conn)
    return Orchestrator(rt)  # type: ignore[arg-type]


def test_next_ticket_id_starts_at_one(repo_root: Path) -> None:
    orch = _orch(repo_root)
    assert orch.next_ticket_id() == "t001"


def test_quick_create_writes_file_and_db_row(repo_root: Path) -> None:
    orch = _orch(repo_root)
    result = orch.quick_create_ticket("Fix the thing")
    assert result["ticket_id"] == "t001"
    # File created in the canonical .murder/tickets path.
    path = ticket_md(repo_root, "t001")
    assert path.exists()
    assert "Fix the thing" in path.read_text()
    # DB row inserted as PLANNED.
    row = orch.rt.db.execute(  # type: ignore[union-attr]
        "SELECT id, title, status FROM tickets WHERE id = 't001'"
    ).fetchone()
    assert row is not None
    assert row["title"] == "Fix the thing"


def test_next_ticket_id_increments_after_create(repo_root: Path) -> None:
    orch = _orch(repo_root)
    orch.quick_create_ticket("first")
    assert orch.next_ticket_id() == "t002"


def test_ticket_exists_db_and_file_and_negative(repo_root: Path) -> None:
    orch = _orch(repo_root)
    assert orch.ticket_exists("t001") is False
    assert orch.ticket_exists("") is False
    orch.quick_create_ticket("created")
    assert orch.ticket_exists("t001") is True
    # File present but no DB row still counts as existing.
    ticket_md(repo_root, "t099").write_text("# orphan\n")
    assert orch.ticket_exists("t099") is True


async def _noop(*_a: Any, **_k: Any) -> dict[str, Any]:
    return {"handled": True}


def _make_worker(quick_create) -> OrchestratorCommandWorker:
    return OrchestratorCommandWorker(
        kickoff_ready=_noop,
        apply_carve_ready=_noop,
        capture_submit=_noop,
        retry_failed=_noop,
        set_schedule_at=_noop,
        update_metadata=_noop,
        force_status=_noop,
        note_ensure=_noop,
        note_retire=_noop,
        send_agent_message=_noop,
        send_agent_key=_noop,
        refresh_agent_transcript=_noop,
        interrupt_agent=_noop,
        stop_agent=_noop,
        rename_rogue=_noop,
        scaffold_plan=_noop,
        rename_plan=_noop,
        deprecate_plan=_noop,
        quick_kick_ticket=_noop,
        quick_create_ticket=quick_create,
        spawn_rogue=_noop,
        reconfigure_collaborator=_noop,
    )


def _create_command(title: Any) -> CommandEvent:
    return CommandEvent(
        id=uuid4(),
        run_id="run",
        target_worker="orchestrator",
        kind="ticket.quick_create",
        payload={"title": title},
        correlation_id="c",
        idempotency_key="i",
    )


def test_worker_dispatches_quick_create() -> None:
    seen: list[str] = []

    def spy_create(title: str) -> dict[str, Any]:
        seen.append(title)
        return {"handled": True, "ticket_id": "t007", "title": title}

    worker = _make_worker(spy_create)
    ctx = WorkerCtx(repo_root=Path("."))
    result = asyncio.run(worker.on_command(_create_command("hello"), ctx))
    assert result == {"handled": True, "ticket_id": "t007", "title": "hello"}
    assert seen == ["hello"]


def test_worker_rejects_blank_title() -> None:
    worker = _make_worker(lambda _t: {"handled": True})
    ctx = WorkerCtx(repo_root=Path("."))
    with pytest.raises(ValueError, match="ticket.quick_create requires title"):
        asyncio.run(worker.on_command(_create_command("  "), ctx))
