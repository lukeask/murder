"""Runtime-owned sync for `.murder/notetakercontext.md` ↔ SQLite singleton."""

from __future__ import annotations

from pathlib import Path

from murder import db as dbmod
from murder.storage.filesystem import atomic_write_text
from murder.storage.markdown_sync import MarkdownSyncLoop
from murder.storage.paths import notetaker_context_md


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
        dbmod.ensure_notetaker_context_row(self.db)
        row = dbmod.get_notetaker_context(self.db)
        if row is None:
            return
        if not path.exists():
            atomic_write_text(path, str(row["body"]))
        for p in self.scan_paths():
            await self.reconcile_file(p)

    async def reconcile_file(self, path: Path) -> None:
        rel = str(path.relative_to(self.repo_root))
        body = path.read_text(encoding="utf-8")
        dbmod.ensure_notetaker_context_row(self.db)
        row = dbmod.get_notetaker_context(self.db)
        if row is None:
            return
        if str(row["body"]) != body or str(row["materialized_path"]) != rel:
            dbmod.upsert_notetaker_context(self.db, body=body, materialized_path=rel)

    def scan_paths(self) -> list[Path]:
        return [notetaker_context_md(self.repo_root)]
