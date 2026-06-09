"""Runtime-owned notes file synchronization.

Contains two sync loops:
- NoteSync: polls `.murder/notes/*.md` and imports stable edits into SQLite.
- NotetakerContextSync: polls `.murder/notetakercontext.md` singleton.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from murder.work.notes import content_hash
from murder.state.persistence import notes as _notes_db
from murder.state.persistence import notetaker as _notetaker_db
from murder.state.storage.filesystem import atomic_write_text
from murder.state.storage.markdown_loop import MarkdownSyncLoop
from murder.state.storage.paths import note_md, notes_dir, notetaker_context_md

# (note_name) -> emit a key-only ``state.snapshot{entity=note}``. Injected by the
# runtime so this pure parse/DB loop never touches the bus directly; the callback
# funnels the filesystem->DB reconcile path (the PRIMARY note writer) into F1's
# key-only emit. The NoteSync analog of TicketSync's ``on_ticket_change`` and
# PlanSync's ``on_plan_change``. See ``Runtime.emit_snapshot``.
NoteChangeNotifier = Callable[[str], None]


class NoteSync(MarkdownSyncLoop):
    """Poll `.murder/notes/*.md` and import stable file edits into SQLite."""

    def __init__(
        self,
        repo_root: Path,
        db,
        *,
        poll_s: float = 1.5,
        debounce_s: float = 0.75,
        on_note_change: NoteChangeNotifier | None = None,
    ) -> None:
        super().__init__(repo_root, poll_s=poll_s, debounce_s=debounce_s)
        self.db = db
        self.on_note_change = on_note_change

    async def reconcile_all(self) -> None:
        notes_dir(self.repo_root).mkdir(parents=True, exist_ok=True)
        for row in _notes_db.list_notes(self.db):
            path = note_md(self.repo_root, str(row["name"]))
            if not path.exists():
                full = _notes_db.get_note(self.db, str(row["name"]))
                if full is not None:
                    atomic_write_text(path, str(full["body"]))
        for path in self.scan_paths():
            await self.reconcile_file(path)

    async def reconcile_file(self, path: Path) -> None:
        name = path.stem
        rel = str(path.relative_to(self.repo_root))
        body = path.read_text(encoding="utf-8")
        row = _notes_db.get_note(self.db, name)
        if row is None:
            _notes_db.upsert_note(self.db, name, body=body, materialized_path=rel)
            _notes_db.insert_note_revision(
                self.db,
                name,
                source="file_import",
                body=body,
                content_hash=content_hash(body),
            )
            # New file import -> a note appeared in the active list. Emit only on
            # this write branch (not the unchanged early-return below). Fires on
            # reconcile_all startup churn too, matching the ticket/plan precedent.
            if self.on_note_change is not None:
                self.on_note_change(name)
            return
        if str(row["body"]) != body or str(row["materialized_path"]) != rel:
            _notes_db.upsert_note(self.db, name, body=body, materialized_path=rel)
            if str(row["body"]) != body:
                _notes_db.insert_note_revision(
                    self.db,
                    name,
                    source="file_import",
                    body=body,
                    content_hash=content_hash(body),
                )
            # Existing note's body/path changed (a visible edit). Emit once.
            if self.on_note_change is not None:
                self.on_note_change(name)

    def scan_paths(self) -> list[Path]:
        return self._scan_paths()

    def _scan_paths(self) -> list[Path]:
        root = notes_dir(self.repo_root)
        if not root.exists():
            return []
        return sorted(p for p in root.glob("*.md") if p.is_file())


class NotetakerContextSync(MarkdownSyncLoop):
    """Poll the context markdown file and import stable edits into SQLite."""

    def __init__(
        self,
        repo_root: Path,
        db,
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
