"""F5.1 + F5.2 — emit seam on MarkdownSyncLoop and reports doc-DAO.

F5.1 assertions:
- ``notify_changed`` fires the async callback when both entity and on_change are set.
- ``notify_changed`` is silent when entity is None.
- ``notify_changed`` is silent when on_change is None.

F5.2 assertions:
- Reports rows round-trip through upsert → get → insert_revision → list_revisions.
- rename_report transfers the FK in report_revisions.
- mark_report_retired marks the row correctly.
- Notes behaviour is unchanged (thin-binding smoke test via existing public names).
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from murder.bus.protocol import Entity
from murder.state.persistence.schema import init_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _conn() -> sqlite3.Connection:
    # isolation_level=None puts sqlite3 in autocommit mode, matching the
    # production get_db() call so that explicit BEGIN/COMMIT in rename_doc work.
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


# ---------------------------------------------------------------------------
# F5.1 — emit seam
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_changed_fires_callback_when_both_set() -> None:
    from murder.state.storage.markdown_loop import MarkdownSyncLoop
    from pathlib import Path

    fired: list[tuple[Entity, str]] = []

    async def cb(entity: Entity, key: str) -> None:
        fired.append((entity, key))

    # Use a minimal concrete subclass — abstract methods are no-ops.
    class _Loop(MarkdownSyncLoop):
        async def reconcile_all(self) -> None:
            pass

        async def reconcile_file(self, path: Path) -> None:
            pass

        def scan_paths(self) -> list[Path]:
            return []

    loop = _Loop(Path("/tmp"), entity=Entity.NOTE, on_change=cb)
    await loop.notify_changed("my-note")

    assert fired == [(Entity.NOTE, "my-note")]


@pytest.mark.asyncio
async def test_notify_changed_silent_when_entity_is_none() -> None:
    from murder.state.storage.markdown_loop import MarkdownSyncLoop

    fired: list[object] = []

    async def cb(entity: Entity, key: str) -> None:
        fired.append((entity, key))

    class _Loop(MarkdownSyncLoop):
        async def reconcile_all(self) -> None:
            pass

        async def reconcile_file(self, path: Path) -> None:
            pass

        def scan_paths(self) -> list[Path]:
            return []

    loop = _Loop(Path("/tmp"), entity=None, on_change=cb)
    await loop.notify_changed("some-key")

    assert fired == []


@pytest.mark.asyncio
async def test_notify_changed_silent_when_on_change_is_none() -> None:
    from murder.state.storage.markdown_loop import MarkdownSyncLoop

    class _Loop(MarkdownSyncLoop):
        async def reconcile_all(self) -> None:
            pass

        async def reconcile_file(self, path: Path) -> None:
            pass

        def scan_paths(self) -> list[Path]:
            return []

    loop = _Loop(Path("/tmp"), entity=Entity.NOTE, on_change=None)
    # Should not raise even when called with no callback
    await loop.notify_changed("key")


# ---------------------------------------------------------------------------
# F5.2 — reports DAO round-trip
# ---------------------------------------------------------------------------


def test_reports_upsert_and_get() -> None:
    from murder.state.persistence.reports import get_report, upsert_report

    conn = _conn()
    upsert_report(conn, "alpha", body="# Alpha\n", materialized_path="agents/reports/alpha.md")
    row = get_report(conn, "alpha")
    assert row is not None
    assert row["name"] == "alpha"
    assert row["body"] == "# Alpha\n"
    assert row["status"] == "active"
    assert row["retired_at"] is None


def test_reports_upsert_updates_body() -> None:
    from murder.state.persistence.reports import get_report, upsert_report

    conn = _conn()
    upsert_report(conn, "beta", body="v1", materialized_path="agents/reports/beta.md")
    upsert_report(conn, "beta", body="v2", materialized_path="agents/reports/beta.md")
    row = get_report(conn, "beta")
    assert row is not None
    assert row["body"] == "v2"


def test_reports_list_active_only() -> None:
    from murder.state.persistence.reports import list_reports, mark_report_retired, upsert_report

    conn = _conn()
    upsert_report(conn, "r1", body="", materialized_path="agents/reports/r1.md")
    upsert_report(conn, "r2", body="", materialized_path="agents/reports/r2.md")
    mark_report_retired(conn, "r2", materialized_path="agents/reports/r2.md")
    active = list_reports(conn)
    names = [r["name"] for r in active]
    assert "r1" in names
    assert "r2" not in names


def test_reports_list_returns_size_not_body() -> None:
    """list_reports must project size=len(body), not raw body — contract parity."""
    from murder.state.persistence.reports import list_reports, upsert_report

    conn = _conn()
    upsert_report(conn, "sz", body="hello", materialized_path="agents/reports/sz.md")
    rows = list_reports(conn)
    assert len(rows) == 1
    assert rows[0]["size"] == len("hello")
    assert "body" not in rows[0]


def test_report_revision_round_trip() -> None:
    from murder.state.persistence.reports import (
        insert_report_revision,
        list_report_revisions,
        upsert_report,
    )

    conn = _conn()
    upsert_report(conn, "gamma", body="v1", materialized_path="agents/reports/gamma.md")
    insert_report_revision(conn, "gamma", source="file_import", body="v1", content_hash="h1")
    insert_report_revision(conn, "gamma", source="agent", body="v2", content_hash="h2")
    revs = list_report_revisions(conn, "gamma")
    assert len(revs) == 2
    assert revs[0]["source"] == "file_import"
    assert revs[1]["source"] == "agent"
    # FK column must be report_name, not note_name
    assert "report_name" in revs[0]
    assert revs[0]["report_name"] == "gamma"


def test_rename_report_transfers_revisions() -> None:
    from murder.state.persistence.reports import (
        get_report,
        insert_report_revision,
        list_report_revisions,
        rename_report,
        upsert_report,
    )

    conn = _conn()
    upsert_report(conn, "old", body="body", materialized_path="agents/reports/old.md")
    insert_report_revision(conn, "old", source="file_import", body="body", content_hash="h")
    rename_report(conn, "old", "new", materialized_path="agents/reports/new.md")

    assert get_report(conn, "old") is None
    row = get_report(conn, "new")
    assert row is not None
    assert row["name"] == "new"

    revs = list_report_revisions(conn, "new")
    assert len(revs) == 1
    assert revs[0]["report_name"] == "new"


def test_mark_report_retired() -> None:
    from murder.state.persistence.reports import get_report, mark_report_retired, upsert_report

    conn = _conn()
    upsert_report(conn, "ret", body="", materialized_path="agents/reports/ret.md")
    mark_report_retired(conn, "ret", materialized_path="agents/reports/ret.md")
    row = get_report(conn, "ret")
    assert row is not None
    assert row["status"] == "retired"
    assert row["retired_at"] is not None


# ---------------------------------------------------------------------------
# F5.2 — notes thin-binding smoke test (preserve existing public names)
# ---------------------------------------------------------------------------


def test_notes_thin_binding_preserves_behaviour() -> None:
    """The re-expressed notes.py must behave identically to before."""
    from murder.state.persistence.notes import (
        get_note,
        insert_note_revision,
        list_note_revisions,
        list_notes,
        mark_note_retired,
        rename_note,
        upsert_note,
    )

    conn = _conn()
    upsert_note(conn, "n1", body="body one", materialized_path=".murder/notes/n1.md")
    upsert_note(conn, "n2", body="body two", materialized_path=".murder/notes/n2.md")

    n1 = get_note(conn, "n1")
    assert n1 is not None
    assert n1["body"] == "body one"

    insert_note_revision(conn, "n1", source="file_import", body="body one", content_hash="h1")
    revs = list_note_revisions(conn, "n1")
    assert len(revs) == 1
    assert "note_name" in revs[0]
    assert revs[0]["note_name"] == "n1"

    # list_notes returns size, not body
    active = list_notes(conn)
    names = [r["name"] for r in active]
    assert "n1" in names and "n2" in names
    assert all("size" in r and "body" not in r for r in active)

    rename_note(conn, "n1", "n1-renamed", materialized_path=".murder/notes/n1-renamed.md")
    assert get_note(conn, "n1") is None
    assert get_note(conn, "n1-renamed") is not None

    mark_note_retired(conn, "n2", materialized_path=".murder/notes/n2.md")
    assert get_note(conn, "n2")["status"] == "retired"
