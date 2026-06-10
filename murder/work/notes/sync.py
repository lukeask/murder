"""Runtime-owned notes file synchronization.

Contains two sync loops:
- NoteSync: polls `.murder/notes/*.md` and imports stable edits into SQLite.
- NotetakerContextSync: polls `.murder/notetakercontext.md` singleton.

``NoteSync`` is now a thin factory wrapper over ``SimpleDocSync`` — the shared
reconcile algorithm lives there.  ``NotetakerContextSync`` remains its own
subclass (singleton, no name/revisions shape).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from murder.bus.protocol import Entity
from murder.state.persistence import notes as _notes_db
from murder.state.persistence import notetaker as _notetaker_db
from murder.state.storage.filesystem import atomic_write_text
from murder.state.storage.markdown_loop import MarkdownSyncLoop
from murder.state.storage.paths import note_md, notes_dir, notetaker_context_md
from murder.work.simple_doc_sync import SimpleDocSync


def NoteSync(
    repo_root: Path,
    db: Any,
    *,
    poll_s: float = 1.5,
    debounce_s: float = 0.75,
    on_change: "Callable[[Entity, str], Any] | None" = None,
) -> SimpleDocSync:
    """Return a ``SimpleDocSync`` configured for ``.murder/notes/*.md``.

    The old ``on_note_change`` sync-callback parameter is replaced by
    ``on_change``, which takes ``(Entity, str)`` and is awaited via the F5.1
    ``notify_changed`` seam.  Pass ``on_change`` from ``FilesystemSyncSupervisor``
    (which provides ``_emit``).
    """
    return SimpleDocSync(
        repo_root,
        db,
        dir_fn=notes_dir,
        md_path_fn=note_md,
        list_fn=_notes_db.list_notes,
        get_fn=_notes_db.get_note,
        upsert_fn=_notes_db.upsert_note,
        insert_revision_fn=_notes_db.insert_note_revision,
        entity=Entity.NOTE,
        poll_s=poll_s,
        debounce_s=debounce_s,
        on_change=on_change,
    )


class NotetakerContextSync(MarkdownSyncLoop):
    """Poll the context markdown file and import stable edits into SQLite."""

    def __init__(
        self,
        repo_root: Path,
        db: Any,
        *,
        poll_s: float = 1.5,
        debounce_s: float = 0.75,
    ) -> None:
        super().__init__(repo_root, poll_s=poll_s, debounce_s=debounce_s)
        self.db = db

    async def reconcile_all(self) -> None:
        path = notetaker_context_md(self.repo_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        _notetaker_db.ensure_notetaker_context_row(self.db)
        row = _notetaker_db.get_notetaker_context(self.db)
        if row is None:
            return
        if not path.exists():
            atomic_write_text(path, str(row["body"]))
        for p in self.scan_paths():
            await self.reconcile_file(p)

    async def reconcile_file(self, path: Path) -> None:
        rel = str(path.relative_to(self.repo_root))
        body = path.read_text(encoding="utf-8")
        _notetaker_db.ensure_notetaker_context_row(self.db)
        row = _notetaker_db.get_notetaker_context(self.db)
        if row is None:
            return
        if str(row["body"]) != body or str(row["materialized_path"]) != rel:
            _notetaker_db.upsert_notetaker_context(self.db, body=body, materialized_path=rel)

    def scan_paths(self) -> list[Path]:
        return [notetaker_context_md(self.repo_root)]
