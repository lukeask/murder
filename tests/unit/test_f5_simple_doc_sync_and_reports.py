"""F5.3 + F5.4 — SimpleDocSync / ReportSync / DB-backed reports snapshot.

Verifies:
(a) One reconcile algorithm backs both notes and reports (behaviour parity):
    - insert path: new file → DB row + revision + emit
    - edit path: changed body → DB update + revision + emit
    - noop path: unchanged file → no DB write, no emit
(b) A stable report edit on disk → ReportSync ingests + emits ``report`` entity.
(c) No double-emit: exactly one StateSnapshotEvent per committed change from the
    supervisor when using the async notify_changed seam.
(d) get_reports_snapshot reads the ``reports`` table, not the filesystem:
    - a DB-only report (no file) appears in the snapshot
    - a disk-only report (no DB row) is absent from the snapshot
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from murder.bus.protocol import Entity, StateSnapshotEvent
from murder.state.persistence.runs import insert_run
from murder.state.persistence.schema import init_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    # Events FK-reference runs(run_id); seed the run these tests publish under so
    # event persistence succeeds (the bus is fail-closed on persistence failure).
    insert_run(conn, "run-test", "{}")
    return conn


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".murder").mkdir()
    return repo


async def _record_async(sink: list[object], ev: object) -> None:
    sink.append(ev)


# ---------------------------------------------------------------------------
# (a) Behaviour parity: NoteSync and ReportSync share the same algorithm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_simple_doc_sync_insert_new_note(tmp_path: Path) -> None:
    """New note file → upserted to DB + revision created + emit fires."""
    from murder.work.notes.sync import NoteSync

    repo = _make_repo(tmp_path)
    conn = _conn()
    emitted: list[tuple[Entity, str]] = []

    async def cb(entity: Entity, key: str) -> None:
        emitted.append((entity, key))

    notes_dir = repo / ".murder" / "notes"
    notes_dir.mkdir()
    note_path = notes_dir / "mynote.md"
    note_path.write_text("# My note\n")

    sync = NoteSync(repo, conn, on_change=cb)
    await sync.reconcile_file(note_path)

    from murder.state.persistence.notes import get_note, list_note_revisions

    row = get_note(conn, "mynote")
    assert row is not None, "note was not inserted"
    assert row["body"] == "# My note\n"
    assert len(list_note_revisions(conn, "mynote")) == 1

    assert len(emitted) == 1
    assert emitted[0] == (Entity.NOTE, "mynote")


@pytest.mark.asyncio
async def test_simple_doc_sync_insert_new_report(tmp_path: Path) -> None:
    """New report file → upserted to DB + revision created + emit fires (parity)."""
    from murder.work.reports.sync import ReportSync

    repo = _make_repo(tmp_path)
    conn = _conn()
    emitted: list[tuple[Entity, str]] = []

    async def cb(entity: Entity, key: str) -> None:
        emitted.append((entity, key))

    reports_dir = repo / ".murder" / "reports"
    reports_dir.mkdir()
    report_path = reports_dir / "sprint-1.md"
    report_path.write_text("# Sprint 1\n")

    sync = ReportSync(repo, conn, on_change=cb)
    await sync.reconcile_file(report_path)

    from murder.state.persistence.reports import get_report, list_report_revisions

    row = get_report(conn, "sprint-1")
    assert row is not None, "report was not inserted"
    assert row["body"] == "# Sprint 1\n"
    assert len(list_report_revisions(conn, "sprint-1")) == 1

    assert len(emitted) == 1
    assert emitted[0] == (Entity.REPORT, "sprint-1")


@pytest.mark.asyncio
async def test_simple_doc_sync_edit_updates_note(tmp_path: Path) -> None:
    """Editing a note file body → update + new revision + emit."""
    from murder.work.notes.sync import NoteSync

    repo = _make_repo(tmp_path)
    conn = _conn()

    notes_dir = repo / ".murder" / "notes"
    notes_dir.mkdir()
    note_path = notes_dir / "edit-note.md"
    note_path.write_text("v1\n")

    seed = NoteSync(repo, conn)
    await seed.reconcile_file(note_path)

    emitted: list[tuple[Entity, str]] = []

    async def cb(entity: Entity, key: str) -> None:
        emitted.append((entity, key))

    note_path.write_text("v2\n")
    sync = NoteSync(repo, conn, on_change=cb)
    await sync.reconcile_file(note_path)

    from murder.state.persistence.notes import get_note, list_note_revisions

    assert get_note(conn, "edit-note")["body"] == "v2\n"  # type: ignore[index]
    assert len(list_note_revisions(conn, "edit-note")) == 2
    assert len(emitted) == 1
    assert emitted[0] == (Entity.NOTE, "edit-note")


@pytest.mark.asyncio
async def test_simple_doc_sync_edit_updates_report(tmp_path: Path) -> None:
    """Editing a report file body → update + new revision + emit (parity)."""
    from murder.work.reports.sync import ReportSync

    repo = _make_repo(tmp_path)
    conn = _conn()

    reports_dir = repo / ".murder" / "reports"
    reports_dir.mkdir()
    report_path = reports_dir / "rpt.md"
    report_path.write_text("v1\n")

    seed = ReportSync(repo, conn)
    await seed.reconcile_file(report_path)

    emitted: list[tuple[Entity, str]] = []

    async def cb(entity: Entity, key: str) -> None:
        emitted.append((entity, key))

    report_path.write_text("v2\n")
    sync = ReportSync(repo, conn, on_change=cb)
    await sync.reconcile_file(report_path)

    from murder.state.persistence.reports import get_report, list_report_revisions

    assert get_report(conn, "rpt")["body"] == "v2\n"  # type: ignore[index]
    assert len(list_report_revisions(conn, "rpt")) == 2
    assert len(emitted) == 1
    assert emitted[0] == (Entity.REPORT, "rpt")


@pytest.mark.asyncio
async def test_simple_doc_sync_noop_no_emit_note(tmp_path: Path) -> None:
    """Unchanged note file → no DB write, no emit, no revision bump."""
    from murder.work.notes.sync import NoteSync

    repo = _make_repo(tmp_path)
    conn = _conn()

    notes_dir = repo / ".murder" / "notes"
    notes_dir.mkdir()
    note_path = notes_dir / "stable.md"
    note_path.write_text("stable content\n")

    seed = NoteSync(repo, conn)
    await seed.reconcile_file(note_path)

    from murder.state.persistence.notes import list_note_revisions

    revisions_after_seed = len(list_note_revisions(conn, "stable"))

    emitted: list[tuple[Entity, str]] = []

    async def cb(entity: Entity, key: str) -> None:
        emitted.append((entity, key))

    sync = NoteSync(repo, conn, on_change=cb)
    await sync.reconcile_file(note_path)

    assert emitted == [], "no emit for unchanged note"
    assert len(list_note_revisions(conn, "stable")) == revisions_after_seed, (
        "revision count must not bump on no-op reconcile"
    )


@pytest.mark.asyncio
async def test_simple_doc_sync_noop_no_emit_report(tmp_path: Path) -> None:
    """Unchanged report file → no DB write, no emit, no revision bump (parity)."""
    from murder.work.reports.sync import ReportSync

    repo = _make_repo(tmp_path)
    conn = _conn()

    reports_dir = repo / ".murder" / "reports"
    reports_dir.mkdir()
    report_path = reports_dir / "stable.md"
    report_path.write_text("stable content\n")

    seed = ReportSync(repo, conn)
    await seed.reconcile_file(report_path)

    from murder.state.persistence.reports import list_report_revisions

    revisions_after_seed = len(list_report_revisions(conn, "stable"))

    emitted: list[tuple[Entity, str]] = []

    async def cb(entity: Entity, key: str) -> None:
        emitted.append((entity, key))

    sync = ReportSync(repo, conn, on_change=cb)
    await sync.reconcile_file(report_path)

    assert emitted == [], "no emit for unchanged report"
    assert len(list_report_revisions(conn, "stable")) == revisions_after_seed, (
        "revision count must not bump on no-op reconcile"
    )


# ---------------------------------------------------------------------------
# (b) Full disk→ingest→emit cycle for a report via ReportSync
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_sync_stable_edit_emits_report_entity(tmp_path: Path) -> None:
    """ReportSync.reconcile_file after a stable edit → one REPORT emit."""
    from murder.work.reports.sync import ReportSync

    repo = _make_repo(tmp_path)
    conn = _conn()

    reports_dir_path = repo / ".murder" / "reports"
    reports_dir_path.mkdir()
    p = reports_dir_path / "q3-retro.md"
    p.write_text("# Q3 Retro\n\nInitial draft\n")

    seed = ReportSync(repo, conn)
    await seed.reconcile_file(p)

    emitted: list[tuple[Entity, str]] = []

    async def cb(entity: Entity, key: str) -> None:
        emitted.append((entity, key))

    p.write_text("# Q3 Retro\n\nUpdated body\n")
    sync = ReportSync(repo, conn, on_change=cb)
    await sync.reconcile_file(p)

    assert len(emitted) == 1
    assert emitted[0] == (Entity.REPORT, "q3-retro")


# ---------------------------------------------------------------------------
# (c) No double-emit: exactly ONE StateSnapshotEvent per committed change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_double_emit_note_via_supervisor(tmp_path: Path) -> None:
    """FilesystemSyncSupervisor with bus+run_id wired: note change → exactly 1 event."""
    from murder.app.service.filesystem_sync import FilesystemSyncSupervisor
    from murder.bus import Bus

    repo = _make_repo(tmp_path)
    conn = _conn()
    bus = Bus("run-test", conn)

    captured: list[StateSnapshotEvent] = []

    async def sink(ev: object) -> None:
        if isinstance(ev, StateSnapshotEvent):
            captured.append(ev)

    bus.subscribe(lambda ev: sink(ev))

    sup = FilesystemSyncSupervisor.attach(
        repo,
        conn,
        bus=bus,
        run_id="run-test",
    )

    notes_dir = repo / ".murder" / "notes"
    notes_dir.mkdir(parents=True)
    note_path = notes_dir / "dedup.md"
    note_path.write_text("first body\n")

    await sup.note_sync.reconcile_file(note_path)
    # Give async tasks a chance to flush
    await asyncio.sleep(0)

    note_events = [e for e in captured if e.entity == Entity.NOTE and e.key == "dedup"]
    assert len(note_events) == 1, f"expected exactly 1 note event, got {len(note_events)}"


@pytest.mark.asyncio
async def test_no_double_emit_report_via_supervisor(tmp_path: Path) -> None:
    """FilesystemSyncSupervisor with bus+run_id wired: report change → exactly 1 event."""
    from murder.app.service.filesystem_sync import FilesystemSyncSupervisor
    from murder.bus import Bus

    repo = _make_repo(tmp_path)
    conn = _conn()
    bus = Bus("run-test", conn)

    captured: list[StateSnapshotEvent] = []

    async def sink(ev: object) -> None:
        if isinstance(ev, StateSnapshotEvent):
            captured.append(ev)

    bus.subscribe(lambda ev: sink(ev))

    sup = FilesystemSyncSupervisor.attach(
        repo,
        conn,
        bus=bus,
        run_id="run-test",
    )

    reports_dir = repo / ".murder" / "reports"
    reports_dir.mkdir(parents=True)
    report_path = reports_dir / "dedup-report.md"
    report_path.write_text("first body\n")

    await sup.report_sync.reconcile_file(report_path)
    await asyncio.sleep(0)

    report_events = [
        e for e in captured if e.entity == Entity.REPORT and e.key == "dedup-report"
    ]
    assert len(report_events) == 1, (
        f"expected exactly 1 report event, got {len(report_events)}"
    )


# ---------------------------------------------------------------------------
# (d) DB-backed get_reports_snapshot — filesystem is NOT consulted
# ---------------------------------------------------------------------------


def test_reports_snapshot_reads_db_not_disk(tmp_path: Path) -> None:
    """get_reports_snapshot returns DB rows; disk-only reports are absent."""
    from murder.app.service.read_model import ServiceReadModel
    from murder.state.persistence.reports import upsert_report

    db_path = tmp_path / ".murder" / "murder.db"
    db_path.parent.mkdir(parents=True)
    conn = _conn()
    conn.close()

    # Init a fresh DB at the expected path
    import sqlite3 as _sqlite3

    conn2 = _sqlite3.connect(str(db_path), isolation_level=None)
    conn2.row_factory = _sqlite3.Row
    init_db(conn2)

    # Insert a report row (no file on disk)
    upsert_report(conn2, "db-only", body="# DB only\n", materialized_path=".murder/reports/db-only.md")
    conn2.close()

    # Also create a disk-only file (no DB row)
    reports_dir = tmp_path / ".murder" / "reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "disk-only.md").write_text("# Disk only\n")

    rm = ServiceReadModel(db_path)
    snap = rm.get_reports_snapshot()

    names = {r.name for r in snap.reports}
    assert "db-only" in names, "DB-backed report must appear in snapshot"
    assert "disk-only" not in names, "Disk-only report must NOT appear (no DB row)"


def test_reports_snapshot_excludes_retired_reports(tmp_path: Path) -> None:
    """Retired reports do not appear in the snapshot (mirrors notes behaviour)."""
    from murder.app.service.read_model import ServiceReadModel
    from murder.state.persistence.reports import (
        mark_report_retired,
        upsert_report,
    )

    db_path = tmp_path / ".murder" / "murder.db"
    db_path.parent.mkdir(parents=True)

    import sqlite3 as _sqlite3

    conn = _sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = _sqlite3.Row
    init_db(conn)
    upsert_report(conn, "active-rpt", body="body", materialized_path=".murder/reports/active-rpt.md")
    upsert_report(conn, "retired-rpt", body="body", materialized_path=".murder/reports/retired-rpt.md")
    mark_report_retired(conn, "retired-rpt", materialized_path=".murder/reports/retired-rpt.md")
    conn.close()

    rm = ServiceReadModel(db_path)
    snap = rm.get_reports_snapshot()

    names = {r.name for r in snap.reports}
    assert "active-rpt" in names
    assert "retired-rpt" not in names
