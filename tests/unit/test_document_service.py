"""DocumentService typed-target coverage (§6.14 / §9)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from murder.app.service.documents import (
    DocumentService,
    NoteDocument,
    PlanDocument,
    ReportDocument,
)
from tests.support.database import open_test_repo_db


def _service(tmp_path: Path, *, plan_sync=None, note_sync=None) -> DocumentService:
    db = open_test_repo_db(tmp_path / "murder.db")
    return DocumentService(
        repo_root=tmp_path,
        db=db,
        plan_sync=plan_sync if plan_sync is not None else MagicMock(),
        note_sync=note_sync if note_sync is not None else MagicMock(),
    )


def test_plan_path_uses_default_when_row_absent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assert service.path_for(PlanDocument("alpha")) == tmp_path / ".murder" / "plans" / "alpha.md"


def test_report_path_creates_reports_dir(tmp_path: Path) -> None:
    service = _service(tmp_path)
    path = service.path_for(ReportDocument("r1"))
    assert path == tmp_path / ".murder" / "reports" / "r1.md"
    assert path.parent.is_dir()


def test_note_path_ensures_note(tmp_path: Path) -> None:
    service = _service(tmp_path)
    path = service.path_for(NoteDocument("n1"))
    assert path == tmp_path / ".murder" / "notes" / "n1.md"
    assert path.exists()


async def test_open_external_plan_reconciles_before_and_after(tmp_path: Path, monkeypatch) -> None:
    plan_sync = MagicMock()
    plan_sync.reconcile_name = AsyncMock()
    service = _service(tmp_path, plan_sync=plan_sync)
    (tmp_path / ".murder" / "plans").mkdir(parents=True)
    (tmp_path / ".murder" / "plans" / "p.md").write_text("# p\n")

    async def _open_editor(path, editor):
        del path, editor
        return 0

    monkeypatch.setattr("murder.app.service.documents.open_editor", _open_editor)
    monkeypatch.setattr("murder.app.service.documents.choose_editor", lambda _pref: "vi")

    code = await service.open_external(PlanDocument("p"))
    assert code == 0
    assert plan_sync.reconcile_name.await_count == 2
