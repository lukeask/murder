"""Plan/note path resolution and editor launch (W3 Runtime narrow)."""

from __future__ import annotations

import shlex
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from murder import notes as notes_mod
from murder.persistence.plans import get_plan_row as _db_get_plan_row
from murder.plans.sync import choose_editor, open_editor
from murder.storage.paths import note_md, report_md, reports_dir

if TYPE_CHECKING:
    from murder.notes.sync import NoteSync
    from murder.plans.sync import PlanSync


@dataclass
class DocumentAccess:
    """Filesystem paths and blocking editor for plans and notes."""

    repo_root: Path
    db: sqlite3.Connection | None = None
    plan_sync: PlanSync | None = None
    note_sync: NoteSync | None = None

    async def reconcile_plan(self, name: str) -> None:
        if self.plan_sync is not None:
            await self.plan_sync.reconcile_name(name)

    def plan_path_for(self, name: str) -> Path:
        row = _db_get_plan_row(self.db, name) if self.db is not None else None
        return (
            self.repo_root / row["materialized_path"]
            if row
            else self.repo_root / ".murder" / "plans" / f"{name}.md"
        )

    def note_path_for(self, name: str) -> Path:
        if self.db is None:
            raise RuntimeError("database not available")
        notes_mod.ensure_note(self.db, self.repo_root, name)
        return note_md(self.repo_root, name)

    def report_path_for(self, name: str) -> Path:
        reports_dir(self.repo_root).mkdir(parents=True, exist_ok=True)
        return report_md(self.repo_root, name)

    def open_editor_blocking(self, path: Path, preferred_editor: str | None = None) -> int:
        editor = choose_editor(preferred_editor)
        argv = shlex.split(editor) or ["vi"]
        proc = subprocess.run([*argv, str(path)], check=False)
        return int(proc.returncode)

    async def open_plan_in_editor(self, name: str, preferred_editor: str | None = None) -> int:
        if self.plan_sync is None:
            raise RuntimeError("plan sync not available")
        await self.plan_sync.reconcile_name(name)
        path = self.plan_path_for(name)
        editor = choose_editor(preferred_editor)
        code = await open_editor(path, editor)
        await self.plan_sync.reconcile_name(name)
        return code

    async def open_note_in_editor(self, name: str, preferred_editor: str | None = None) -> int:
        path = self.note_path_for(name)
        editor = choose_editor(preferred_editor)
        code = await open_editor(path, editor)
        if self.note_sync is not None:
            await self.note_sync.reconcile_file(path)
        return code

    async def open_report_in_editor(self, name: str, preferred_editor: str | None = None) -> int:
        path = self.report_path_for(name)
        editor = choose_editor(preferred_editor)
        return await open_editor(path, editor)


__all__ = ["DocumentAccess"]
