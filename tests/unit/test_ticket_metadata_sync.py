from __future__ import annotations

import sqlite3

import pytest
import yaml

from murder.tickets.meta_sync import TicketMetadataSync


def _add_metadata_columns(conn: sqlite3.Connection) -> None:
    existing = {
        str(r["name"]) for r in conn.execute("PRAGMA table_info(tickets)").fetchall()
    }
    if "schedule_at" not in existing:
        conn.execute("ALTER TABLE tickets ADD COLUMN schedule_at TEXT")
    if "metadata_hash" not in existing:
        conn.execute("ALTER TABLE tickets ADD COLUMN metadata_hash TEXT")
    if "metadata_file_hash" not in existing:
        conn.execute("ALTER TABLE tickets ADD COLUMN metadata_file_hash TEXT")
    if "metadata_last_materialized_hash" not in existing:
        conn.execute("ALTER TABLE tickets ADD COLUMN metadata_last_materialized_hash TEXT")
    if "metadata_materialized_path" not in existing:
        conn.execute("ALTER TABLE tickets ADD COLUMN metadata_materialized_path TEXT")
    if "metadata_sync_state" not in existing:
        conn.execute("ALTER TABLE tickets ADD COLUMN metadata_sync_state TEXT")
    if "metadata_parse_error" not in existing:
        conn.execute("ALTER TABLE tickets ADD COLUMN metadata_parse_error TEXT")
    if "metadata_conflict_reason" not in existing:
        conn.execute("ALTER TABLE tickets ADD COLUMN metadata_conflict_reason TEXT")


@pytest.mark.asyncio
async def test_materialize_missing_yaml_for_db_ticket(tmp_path, memdb: sqlite3.Connection) -> None:
    _add_metadata_columns(memdb)
    memdb.execute(
        """
        INSERT INTO tickets(
            id, title, wave, status, harness, model, attempts, created_at, updated_at, schedule_at
        )
        VALUES ('t101', 'Materialize me', 2, 'ready', 'codex', 'gpt-5', 0,
                '2026-05-15T10:00:00', '2026-05-15T10:00:00', '2026-05-15T18:30:00-04:00')
        """
    )
    memdb.execute(
        """
        INSERT INTO tickets(
            id, title, wave, status, harness, model, attempts, created_at, updated_at
        )
        VALUES ('t100', 'dep', 1, 'done', NULL, NULL, 0,
                '2026-05-15T09:00:00', '2026-05-15T09:00:00')
        """
    )
    memdb.execute("INSERT INTO ticket_deps(ticket_id, depends_on_id) VALUES ('t101', 't100')")
    memdb.execute(
        "INSERT INTO ticket_write_set(ticket_id, path) VALUES ('t101', 'murder/runtime.py')"
    )
    memdb.execute("INSERT INTO ticket_skills(ticket_id, skill) VALUES ('t101', 'openai-docs')")
    memdb.execute(
        "INSERT INTO checklist(ticket_id, ord, text, done) "
        "VALUES ('t101', 0, 'check me', 0)"
    )
    sync = TicketMetadataSync(tmp_path, memdb)

    await sync.reconcile_all()

    path = tmp_path / ".murder" / "tickets" / "t101.yaml"
    assert path.exists()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["id"] == "t101"
    assert data["title"] == "Materialize me"
    assert data["status"] == "ready"
    assert data["deps"] == ["t100"]
    assert data["schedule_at"] == "2026-05-15T18:30:00-04:00"


@pytest.mark.asyncio
async def test_import_yaml_creates_missing_db_ticket(tmp_path, memdb: sqlite3.Connection) -> None:
    path = tmp_path / ".murder" / "tickets" / "t102.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "id": "t102",
                "title": "Create from yaml",
                "wave": 3,
                "status": "ready",
                "harness": "codex",
                "model": "gpt-5",
                "deps": [],
                "skills": ["imagegen"],
                "write_set": ["murder/tickets/meta_sync.py"],
                "checklist": ["first"],
                "schedule_at": None,
            },
            sort_keys=False,
            allow_unicode=False,
        ),
        encoding="utf-8",
    )
    sync = TicketMetadataSync(tmp_path, memdb)

    await sync.reconcile_all()

    row = memdb.execute(
        "SELECT id, title, wave, status, harness, model FROM tickets WHERE id = 't102'"
    ).fetchone()
    assert row is not None
    assert row["status"] == "ready"
    deps = memdb.execute(
        "SELECT COUNT(*) AS n FROM ticket_deps WHERE ticket_id = 't102'"
    ).fetchone()
    assert int(deps["n"]) == 0
    checklist = memdb.execute(
        "SELECT text FROM checklist WHERE ticket_id = 't102' ORDER BY ord"
    ).fetchall()
    assert [r["text"] for r in checklist] == ["first"]


@pytest.mark.asyncio
async def test_invalid_yaml_sets_parse_error_state(tmp_path, memdb: sqlite3.Connection) -> None:
    _add_metadata_columns(memdb)
    memdb.execute(
        """
        INSERT INTO tickets(
            id, title, wave, status, harness, model, attempts, created_at, updated_at
        )
        VALUES ('t103', 'Parse target', 1, 'planned', NULL, NULL, 0,
                '2026-05-15T10:00:00', '2026-05-15T10:00:00')
        """
    )
    path = tmp_path / ".murder" / "tickets" / "t103.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("id: t103\ntitle: nope\nwave: nope\nstatus: planned\n", encoding="utf-8")
    sync = TicketMetadataSync(tmp_path, memdb)

    await sync.reconcile_file(path)

    row = memdb.execute(
        "SELECT metadata_sync_state, metadata_parse_error FROM tickets WHERE id = 't103'"
    ).fetchone()
    assert row["metadata_sync_state"] == "parse_error"
    assert "wave must be an integer" in row["metadata_parse_error"]


@pytest.mark.asyncio
async def test_db_owned_status_is_not_mutated_from_yaml(
    tmp_path, memdb: sqlite3.Connection
) -> None:
    _add_metadata_columns(memdb)
    memdb.execute(
        """
        INSERT INTO tickets(
            id, title, wave, status, harness, model, attempts, created_at, updated_at
        )
        VALUES ('t104', 'Runtime owns status', 2, 'in_progress', 'codex', 'gpt-5', 0,
                '2026-05-15T10:00:00', '2026-05-15T10:00:00')
        """
    )
    path = tmp_path / ".murder" / "tickets" / "t104.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "id": "t104",
                "title": "Runtime owns status",
                "wave": 2,
                "status": "ready",
                "deps": [],
                "skills": [],
                "write_set": [],
                "checklist": [],
                "schedule_at": None,
            },
            sort_keys=False,
            allow_unicode=False,
        ),
        encoding="utf-8",
    )
    sync = TicketMetadataSync(tmp_path, memdb)

    await sync.reconcile_file(path)

    status = memdb.execute(
        "SELECT status, metadata_sync_state, metadata_conflict_reason "
        "FROM tickets WHERE id = 't104'"
    ).fetchone()
    assert status["status"] == "in_progress"
    assert status["metadata_sync_state"] == "conflict"
    assert "DB-owned" in str(status["metadata_conflict_reason"])
    reloaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert reloaded["status"] == "in_progress"
