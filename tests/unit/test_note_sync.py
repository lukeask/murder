"""Notes file/db synchronization tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from murder import notes
from murder.notes_sync import NoteSync


@pytest.mark.asyncio
async def test_reconcile_all_imports_existing_note_file(memdb, tmp_path: Path) -> None:
    path = tmp_path / ".murder" / "notes" / "2026-05-13.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Imported\n- from disk", encoding="utf-8")

    sync = NoteSync(tmp_path, memdb, poll_s=3600.0, debounce_s=0.01)
    await sync.reconcile_all()

    assert notes.read_note(memdb, "2026-05-13") == "# Imported\n- from disk"
    revs = memdb.execute(
        "SELECT source, body FROM note_revisions WHERE note_name = ? ORDER BY id",
        ("2026-05-13",),
    ).fetchall()
    assert [(r["source"], r["body"]) for r in revs] == [
        ("file_import", "# Imported\n- from disk")
    ]


@pytest.mark.asyncio
async def test_reconcile_file_updates_db_and_appends_revision(memdb, tmp_path: Path) -> None:
    notes.write_note(memdb, tmp_path, "2026-05-13", "before")
    path = tmp_path / ".murder" / "notes" / "2026-05-13.md"
    path.write_text("after", encoding="utf-8")

    sync = NoteSync(tmp_path, memdb, poll_s=3600.0, debounce_s=0.01)
    await sync.reconcile_file(path)

    assert notes.read_note(memdb, "2026-05-13") == "after"
    revisions = memdb.execute(
        "SELECT source, body FROM note_revisions WHERE note_name = ? ORDER BY id",
        ("2026-05-13",),
    ).fetchall()
    assert [(r["source"], r["body"]) for r in revisions][-1] == ("file_import", "after")

