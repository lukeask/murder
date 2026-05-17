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


def test_retire_note_moves_file_and_removes_from_active_list(memdb, tmp_path: Path) -> None:
    notes.write_note(memdb, tmp_path, "rate-limit-recovery", "body\n")
    dest = notes.retire_note(memdb, tmp_path, "rate-limit-recovery")

    assert dest == tmp_path / ".murder" / "notes" / "retired_notes" / "rate-limit-recovery.md"
    assert dest.read_text(encoding="utf-8") == "body\n"
    assert not (tmp_path / ".murder" / "notes" / "rate-limit-recovery.md").exists()
    assert [row["name"] for row in memdb.execute("SELECT name FROM notes").fetchall()] == [
        "rate-limit-recovery"
    ]
    assert notes.latest_prior_note(memdb, exclude="x") is None
