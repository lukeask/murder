"""Ticket markdown -> DB synchronization tests."""

from __future__ import annotations

import sqlite3

import pytest

from murder import db as dbmod
from murder.tickets.sync import TicketSync

EXISTING_WAVE = 7
EXISTING_ATTEMPTS = 3


@pytest.mark.asyncio
async def test_reconcile_all_imports_orphan_ticket_markdown(
    tmp_path, memdb: sqlite3.Connection
) -> None:
    path = tmp_path / ".murder" / "tickets" / "t012.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "## Plan",
                "Import ticket from markdown into DB.",
                "",
                "## Working notes",
                "",
                "## Sentinel notes",
                "",
            ]
        ),
        encoding="utf-8",
    )

    sync = TicketSync(tmp_path, memdb)
    await sync.reconcile_all()

    row = dbmod.get_ticket(memdb, "t012")
    assert row is not None
    assert row["status"] == "planned"
    assert row["wave"] >= 1
    assert row["title"] == "Import ticket from markdown into DB."


@pytest.mark.asyncio
async def test_reconcile_file_does_not_overwrite_existing_ticket_row(
    tmp_path, memdb: sqlite3.Connection
) -> None:
    memdb.execute(
        """
        INSERT INTO tickets(
            id, title, wave, status, harness, model, attempts, created_at, updated_at
        )
        VALUES (
            't013', 'Existing title', ?, 'in_progress', 'codex', 'gpt-5', ?,
            '2026-05-14T00:00:00', '2026-05-14T00:00:00'
        )
        """,
        (EXISTING_WAVE, EXISTING_ATTEMPTS),
    )
    path = tmp_path / ".murder" / "tickets" / "t013.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "## Plan",
                "This should not replace metadata.",
                "",
                "## Working notes",
                "edited externally",
                "",
            ]
        ),
        encoding="utf-8",
    )

    sync = TicketSync(tmp_path, memdb)
    await sync.reconcile_file(path)

    row = dbmod.get_ticket(memdb, "t013")
    assert row is not None
    assert row["title"] == "Existing title"
    assert row["wave"] == EXISTING_WAVE
    assert row["status"] == "in_progress"
    assert row["harness"] == "codex"
    assert row["model"] == "gpt-5"
    assert row["attempts"] == EXISTING_ATTEMPTS
    assert memdb.execute("SELECT COUNT(*) AS n FROM tickets WHERE id = 't013'").fetchone()["n"] == 1


@pytest.mark.asyncio
async def test_poll_imports_new_ticket_file_and_updates_tui_query_paths(
    tmp_path, memdb: sqlite3.Connection
) -> None:
    path = tmp_path / ".murder" / "tickets" / "t014.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "## Plan",
                "Appears in ticket grid/header queries.",
                "",
                "## Working notes",
                "",
                "## Sentinel notes",
                "",
            ]
        ),
        encoding="utf-8",
    )

    sync = TicketSync(tmp_path, memdb, debounce_s=0)
    await sync.poll_once()
    assert dbmod.get_ticket(memdb, "t014") is None

    await sync.poll_once()
    assert dbmod.get_ticket(memdb, "t014") is not None

    # Query shape used by TicketGrid.refresh_from_db.
    rows = memdb.execute("SELECT id, title, wave, status FROM tickets ORDER BY wave, id").fetchall()
    assert any(r["id"] == "t014" for r in rows)

    # Query shape used by Header.refresh_counts.
    planned_count = memdb.execute(
        "SELECT COUNT(*) AS c FROM tickets WHERE status = ?",
        ("planned",),
    ).fetchone()
    assert int(planned_count["c"]) >= 1


@pytest.mark.asyncio
async def test_reconcile_all_imports_slug_style_ticket_id(
    tmp_path, memdb: sqlite3.Connection
) -> None:
    path = tmp_path / ".murder" / "tickets" / "T01-scaffold.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "# Scaffold",
                "",
                "## Plan",
                "Bootstrap project skeleton.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    sync = TicketSync(tmp_path, memdb)
    await sync.reconcile_all()

    row = dbmod.get_ticket(memdb, "T01-scaffold")
    assert row is not None
    assert row["status"] == "planned"
    assert row["title"] == "Scaffold"


@pytest.mark.asyncio
async def test_reconcile_all_imports_numeric_prefix_ticket_id(
    tmp_path, memdb: sqlite3.Connection
) -> None:
    """Stems like `01-msg-types` start with a digit; they must still import."""
    path = tmp_path / ".murder" / "tickets" / "01-msg-types.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "## Plan",
                "Ordered ticket stem.",
                "",
                "## Working notes",
                "",
                "## Sentinel notes",
                "",
            ]
        ),
        encoding="utf-8",
    )

    sync = TicketSync(tmp_path, memdb)
    await sync.reconcile_all()

    row = dbmod.get_ticket(memdb, "01-msg-types")
    assert row is not None
    assert row["status"] == "planned"
    assert row["title"] == "Ordered ticket stem."
