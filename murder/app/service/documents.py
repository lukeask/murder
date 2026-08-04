"""Plan/note/report path resolution and external editor launch."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from murder.state.persistence.connection import RepoDb
from murder.state.persistence.plans import get_plan_row as _db_get_plan_row
from murder.state.storage.paths import note_md, report_md, reports_dir
from murder.work import notes as notes_mod
from murder.work.plans.sync import PlanSync, choose_editor, open_editor
from murder.work.simple_doc_sync import SimpleDocSync


@dataclass(frozen=True)
class PlanDocument:
    name: str


@dataclass(frozen=True)
class NoteDocument:
    name: str


@dataclass(frozen=True)
class ReportDocument:
    name: str


DocumentTarget = PlanDocument | NoteDocument | ReportDocument


@dataclass
class DocumentService:
    """Filesystem paths and blocking/external editor for Murder documents.

    Constructed once startup has DB and filesystem sync available — no nullable
    infrastructure fields (§11).
    """

    repo_root: Path
    db: RepoDb
    plan_sync: PlanSync
    note_sync: SimpleDocSync

    def path_for(self, target: DocumentTarget) -> Path:
        match target:
            case PlanDocument(name=name):
                row = _db_get_plan_row(self.db, name)
                return (
                    self.repo_root / row["materialized_path"]
                    if row
                    else self.repo_root / ".murder" / "plans" / f"{name}.md"
                )
            case NoteDocument(name=name):
                notes_mod.ensure_note(self.db, self.repo_root, name)
                return note_md(self.repo_root, name)
            case ReportDocument(name=name):
                reports_dir(self.repo_root).mkdir(parents=True, exist_ok=True)
                return report_md(self.repo_root, name)

    async def reconcile(self, target: DocumentTarget) -> None:
        match target:
            case PlanDocument(name=name):
                await self.plan_sync.reconcile_name(name)
            case NoteDocument(name=name):
                await self.note_sync.reconcile_file(self.path_for(target))
            case ReportDocument():
                return

    def open_external_blocking(
        self,
        path: Path,
        *,
        preferred_editor: str | None = None,
    ) -> int:
        editor = choose_editor(preferred_editor)
        argv = shlex.split(editor) or ["vi"]
        proc = subprocess.run([*argv, str(path)], check=False)
        return int(proc.returncode)

    async def open_external(
        self,
        target: DocumentTarget,
        *,
        preferred_editor: str | None = None,
    ) -> int:
        match target:
            case PlanDocument(name=name):
                await self.plan_sync.reconcile_name(name)
                path = self.path_for(target)
                editor = choose_editor(preferred_editor)
                code = await open_editor(path, editor)
                await self.plan_sync.reconcile_name(name)
                return code
            case NoteDocument():
                path = self.path_for(target)
                editor = choose_editor(preferred_editor)
                code = await open_editor(path, editor)
                await self.note_sync.reconcile_file(path)
                return code
            case ReportDocument():
                path = self.path_for(target)
                editor = choose_editor(preferred_editor)
                return await open_editor(path, editor)


__all__ = [
    "DocumentService",
    "DocumentTarget",
    "NoteDocument",
    "PlanDocument",
    "ReportDocument",
]
