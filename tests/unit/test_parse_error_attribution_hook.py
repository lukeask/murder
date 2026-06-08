"""Malformed `.murder/` artifacts re-prompt their owning agent (C12)."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from murder.app.service.filesystem_sync import FilesystemSyncSupervisor
from murder.state.persistence import plans as dbmod
from murder.state.persistence.schema import get_db, init_db
from murder.state.storage.paths import plan_md, ticket_md, tickets_dir
from murder.work.plans.schema import Plan
from murder.work.plans.sync import PlanSync
from murder.work.tickets.sync import TicketSync


def _conn(repo_root: Path):
    db_file = repo_root / ".murder" / "murder.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db(db_file)
    init_db(conn)
    return conn


def _insert_ticket(conn, ticket_id: str) -> None:
    conn.execute(
        """
        INSERT INTO tickets(
            id, title, status, harness, model, attempts, created_at, updated_at
        )
        VALUES (?, 'T', 'planned', 'codex', 'gpt-5', 0,
                '2026-06-08T00:00:00', '2026-06-08T00:00:00')
        """,
        (ticket_id,),
    )


def _seed_plan(conn, repo_root: Path, name: str) -> Path:
    path = plan_md(repo_root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    plan = Plan(name=name, created_at=datetime(2026, 6, 8), frontmatter={"name": name}, body="ok")
    dbmod.upsert_plan(
        conn,
        plan,
        content_hash="h",
        materialized_path=str(path.relative_to(repo_root)),
        file_hash="h",
        sync_state="synced",
        create_revision=True,
        revision_source="import",
    )
    return path


_MALFORMED_TICKET = """---
title: [unterminated
: : :
---
body
"""

_MALFORMED_PLAN = """---
name: [oops
: : :
---
body
"""


def test_malformed_ticket_edit_notifies_owning_crow(repo_root: Path) -> None:
    conn = _conn(repo_root)
    _insert_ticket(conn, "t001")
    calls: list[tuple[Path, str]] = []

    async def notifier(path: Path, err: str) -> None:
        calls.append((path, err))

    sync = TicketSync(repo_root, conn, parse_error_notifier=notifier)
    path = ticket_md(repo_root, "t001")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_MALFORMED_TICKET, encoding="utf-8")

    asyncio.run(sync.reconcile_file(path))

    assert len(calls) == 1
    assert calls[0][0] == path
    assert calls[0][1]
    row = conn.execute("SELECT metadata_sync_state FROM tickets WHERE id='t001'").fetchone()
    assert row["metadata_sync_state"] == "parse_error"


def test_reconcile_all_does_not_notify(repo_root: Path) -> None:
    """The startup bulk pass must not re-prompt agents for idle malformed files."""
    conn = _conn(repo_root)
    _insert_ticket(conn, "t001")
    calls: list[tuple[Path, str]] = []

    async def notifier(path: Path, err: str) -> None:
        calls.append((path, err))

    sync = TicketSync(repo_root, conn, parse_error_notifier=notifier)
    tickets_dir(repo_root).mkdir(parents=True, exist_ok=True)
    ticket_md(repo_root, "t001").write_text(_MALFORMED_TICKET, encoding="utf-8")

    asyncio.run(sync.reconcile_all())

    assert calls == []


def test_valid_ticket_edit_does_not_notify(repo_root: Path) -> None:
    conn = _conn(repo_root)
    _insert_ticket(conn, "t001")
    calls: list[tuple[Path, str]] = []

    async def notifier(path: Path, err: str) -> None:
        calls.append((path, err))

    sync = TicketSync(repo_root, conn, parse_error_notifier=notifier)
    path = ticket_md(repo_root, "t001")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ntitle: Fine\nharness: codex\nmodel: gpt-5\n---\nbody\n",
        encoding="utf-8",
    )

    asyncio.run(sync.reconcile_file(path))

    assert calls == []


def test_malformed_plan_edit_notifies_owning_planner(repo_root: Path) -> None:
    conn = _conn(repo_root)
    calls: list[tuple[Path, str]] = []

    async def notifier(path: Path, err: str) -> None:
        calls.append((path, err))

    sync = PlanSync(repo_root, conn, parse_error_notifier=notifier)
    path = _seed_plan(conn, repo_root, "demo")
    path.write_text(_MALFORMED_PLAN, encoding="utf-8")

    asyncio.run(sync.reconcile_file(path))

    assert len(calls) == 1
    assert calls[0][0] == path


def test_plan_reconcile_all_does_not_notify(repo_root: Path) -> None:
    conn = _conn(repo_root)
    calls: list[tuple[Path, str]] = []

    async def notifier(path: Path, err: str) -> None:
        calls.append((path, err))

    sync = PlanSync(repo_root, conn, parse_error_notifier=notifier)
    path = _seed_plan(conn, repo_root, "demo")
    path.write_text(_MALFORMED_PLAN, encoding="utf-8")

    asyncio.run(sync.reconcile_all())

    assert calls == []


def test_supervisor_notifier_routes_to_owning_agent(repo_root: Path) -> None:
    """End-to-end composition: malformed ticket → attribute_edit → send_message."""
    conn = _conn(repo_root)
    _insert_ticket(conn, "t001")
    sent: list[tuple[str, str]] = []

    async def fake_send(agent_id: str, message: str) -> None:
        sent.append((agent_id, message))

    supervisor = FilesystemSyncSupervisor.attach(repo_root, conn)
    supervisor.set_parse_error_notifier(fake_send)

    path = ticket_md(repo_root, "t001")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_MALFORMED_TICKET, encoding="utf-8")

    asyncio.run(supervisor.ticket_sync.reconcile_file(path))

    assert len(sent) == 1
    agent_id, message = sent[0]
    assert agent_id == "crow-t001"
    assert "t001" in message
    assert str(path) in message


def test_supervisor_notifier_skips_unattributable_path(repo_root: Path) -> None:
    conn = _conn(repo_root)
    sent: list[tuple[str, str]] = []

    async def fake_send(agent_id: str, message: str) -> None:
        sent.append((agent_id, message))

    supervisor = FilesystemSyncSupervisor.attach(repo_root, conn)
    supervisor.set_parse_error_notifier(fake_send)

    # A path attribute_edit cannot attribute (outside tickets/plans dirs).
    await_notifier = supervisor.ticket_sync.parse_error_notifier
    assert await_notifier is not None
    asyncio.run(await_notifier(repo_root / "README.md", "boom"))

    assert sent == []
