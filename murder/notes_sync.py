"""Runtime-owned notes file synchronization."""

from __future__ import annotations

from pathlib import Path

from murder import db as dbmod
from murder import notes
from murder.storage.filesystem import atomic_write_text
from murder.storage.markdown_sync import MarkdownSyncLoop
from murder.storage.paths import note_md, notes_dir


class NoteSync(MarkdownSyncLoop):
    """Poll `.murder/notes/*.md` and import stable file edits into SQLite."""

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
        notes_dir(self.repo_root).mkdir(parents=True, exist_ok=True)
        for row in dbmod.list_notes(self.db):
            path = note_md(self.repo_root, str(row["name"]))
            if not path.exists():
                full = dbmod.get_note(self.db, str(row["name"]))
                if full is not None:
                    atomic_write_text(path, str(full["body"]))
        for path in self.scan_paths():
            await self.reconcile_file(path)

    async def reconcile_file(self, path: Path) -> None:
        name = path.stem
        rel = str(path.relative_to(self.repo_root))
        body = path.read_text(encoding="utf-8")
        row = dbmod.get_note(self.db, name)
        if row is None:
            dbmod.upsert_note(self.db, name, body=body, materialized_path=rel)
            dbmod.insert_note_revision(
                self.db,
                name,
                source="file_import",
                body=body,
                content_hash=notes.content_hash(body),
            )
            return
        if str(row["body"]) != body or str(row["materialized_path"]) != rel:
            dbmod.upsert_note(self.db, name, body=body, materialized_path=rel)
            if str(row["body"]) != body:
                dbmod.insert_note_revision(
                    self.db,
                    name,
                    source="file_import",
                    body=body,
                    content_hash=notes.content_hash(body),
                )

    def scan_paths(self) -> list[Path]:
        return self._scan_paths()

    def _scan_paths(self) -> list[Path]:
        root = notes_dir(self.repo_root)
        if not root.exists():
            return []
        return sorted(p for p in root.glob("*.md") if p.is_file())
