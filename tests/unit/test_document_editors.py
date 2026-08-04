"""DocumentEditorService unit coverage (§6.15 / §9)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from murder.app.service.document_editors import (
    DocumentEditorService,
    EditorDisposition,
    StartDocumentEditor,
    TerminalSize,
    document_target,
)
from murder.app.service.documents import DocumentService, PlanDocument


class FakeEditorTmux:
    def __init__(self) -> None:
        self.sessions: set[str] = set()
        self.created: list[tuple[str, Path, list[str], int, int]] = []
        self.resized: list[tuple[str, int, int]] = []

    async def session_exists(self, name: str) -> bool:
        return name in self.sessions

    async def create_session(
        self,
        name: str,
        cwd: Path,
        cmd: list[str] | None = None,
        *,
        width: int = 220,
        height: int = 50,
    ) -> None:
        self.created.append((name, cwd, list(cmd or []), width, height))
        self.sessions.add(name)

    async def resize_session(self, name: str, *, columns: int, rows: int) -> None:
        self.resized.append((name, columns, rows))


@pytest.fixture
def editor_tmux(monkeypatch: pytest.MonkeyPatch) -> FakeEditorTmux:
    fake = FakeEditorTmux()
    monkeypatch.setattr(
        "murder.app.service.document_editors.tmux.session_exists",
        fake.session_exists,
    )
    monkeypatch.setattr(
        "murder.app.service.document_editors.tmux.create_session",
        fake.create_session,
    )
    monkeypatch.setattr(
        "murder.app.service.document_editors.tmux.resize_session",
        fake.resize_session,
    )
    return fake


class _StubSessions:
    """Records SessionService registration calls for §6.15 assertions."""

    def __init__(self) -> None:
        self.registrations: list[object] = []

    async def ensure_persisted_tmux_session(self, registration) -> object:
        self.registrations.append(registration)
        return object()


def _editors(repo: Path) -> tuple[DocumentEditorService, _StubSessions]:
    from unittest.mock import MagicMock

    from tests.support.database import open_test_repo_db

    db = open_test_repo_db(repo / "murder.db")
    sessions = _StubSessions()
    editors = DocumentEditorService(
        repo,
        DocumentService(
            repo_root=repo,
            db=db,
            plan_sync=MagicMock(),
            note_sync=MagicMock(),
        ),
        sessions=sessions,  # type: ignore[arg-type]
    )
    return editors, sessions


async def test_start_uses_visual_argv_and_canonical_document_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    editor_tmux: FakeEditorTmux,
) -> None:
    repo = tmp_path / "repo with spaces"
    document = repo / ".murder" / "plans" / "plan with spaces.md"
    document.parent.mkdir(parents=True)
    document.write_text("# plan\n")
    monkeypatch.setenv("VISUAL", "/usr/bin/env vim --clean")
    monkeypatch.setenv("EDITOR", "/bin/false")
    editors, sessions = _editors(repo)

    result = await editors.start(
        StartDocumentEditor(
            target=PlanDocument("plan with spaces"),
            size=TerminalSize(73, 19),
        )
    )

    assert result.disposition is EditorDisposition.CREATED
    assert result.document_path == document.resolve()
    assert result.tmux_name.startswith("murder_editor_")
    assert str(document) not in result.tmux_name
    assert editor_tmux.created == [
        (
            result.tmux_name,
            repo.resolve(),
            ["/usr/bin/env", "vim", "--clean", str(document.resolve())],
            73,
            19,
        )
    ]
    assert len(sessions.registrations) == 1
    reg = sessions.registrations[0]
    assert reg.session_id == result.session_id
    assert reg.session_kind == "document_editor"
    assert reg.tmux_name == result.tmux_name


async def test_blank_visual_falls_back_to_editor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    editor_tmux: FakeEditorTmux,
) -> None:
    repo = tmp_path / "repo"
    document = repo / ".murder" / "plans" / "safe.md"
    document.parent.mkdir(parents=True)
    document.write_text("# safe\n")
    monkeypatch.setenv("VISUAL", "   ")
    monkeypatch.setenv("EDITOR", "/bin/true --fallback")
    editors, _sessions = _editors(repo)

    result = await editors.start(
        StartDocumentEditor(target=PlanDocument("safe"), size=TerminalSize(80, 20))
    )

    assert result.disposition is EditorDisposition.CREATED
    assert editor_tmux.created[0][2] == [
        "/bin/true",
        "--fallback",
        str(result.document_path),
    ]


async def test_concurrent_start_reuses_one_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    editor_tmux: FakeEditorTmux,
) -> None:
    repo = tmp_path / "repo"
    document = repo / ".murder" / "plans" / "safe.md"
    document.parent.mkdir(parents=True)
    document.write_text("# safe\n")
    monkeypatch.setenv("VISUAL", "/bin/true")
    editors, sessions = _editors(repo)
    request = StartDocumentEditor(target=PlanDocument("safe"), size=TerminalSize(80, 20))

    first, second = await asyncio.gather(
        editors.start(request),
        editors.start(
            StartDocumentEditor(target=PlanDocument("safe"), size=TerminalSize(90, 25))
        ),
    )

    assert first.session_id == second.session_id
    assert (first.disposition, second.disposition) == (
        EditorDisposition.CREATED,
        EditorDisposition.REUSED,
    )
    assert len(editor_tmux.created) == 1
    assert editor_tmux.resized == [(first.tmux_name, 90, 25)]
    # Created and reused paths both register through SessionService (§6.15).
    assert len(sessions.registrations) == 2
    assert {r.session_id for r in sessions.registrations} == {first.session_id}

    await editors.resize(first.session_id, TerminalSize(91, 27))
    assert editor_tmux.resized[-1] == (first.tmux_name, 91, 27)
    assert not hasattr(editors, "send")
    assert not hasattr(DocumentEditorService, "capture")


async def test_rejects_document_symlinks_outside_the_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    editor_tmux: FakeEditorTmux,
) -> None:
    repo = tmp_path / "repo"
    plans = repo / ".murder" / "plans"
    plans.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("outside\n")
    (plans / "escape.md").symlink_to(outside)
    monkeypatch.setenv("VISUAL", "/bin/true")
    editors, sessions = _editors(repo)

    with pytest.raises(ValueError, match="outside the repository"):
        await editors.start(
            StartDocumentEditor(target=PlanDocument("escape"), size=TerminalSize(80, 20))
        )

    assert editor_tmux.created == []
    assert sessions.registrations == []


@pytest.mark.parametrize(
    ("visual", "editor", "message"),
    [
        (None, None, "no editor configured"),
        ("definitely-not-a-real-editor --flag", "/bin/true", "executable not found"),
        ("'unterminated", "/bin/true", "invalid editor configuration"),
    ],
)
async def test_reports_editor_configuration_errors_before_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    editor_tmux: FakeEditorTmux,
    visual: str | None,
    editor: str | None,
    message: str,
) -> None:
    repo = tmp_path / "repo"
    document = repo / ".murder" / "plans" / "safe.md"
    document.parent.mkdir(parents=True)
    document.write_text("# safe\n")
    if visual is None:
        monkeypatch.delenv("VISUAL", raising=False)
    else:
        monkeypatch.setenv("VISUAL", visual)
    if editor is None:
        monkeypatch.delenv("EDITOR", raising=False)
    else:
        monkeypatch.setenv("EDITOR", editor)
    editors, sessions = _editors(repo)

    with pytest.raises(RuntimeError, match=message):
        await editors.start(
            StartDocumentEditor(target=PlanDocument("safe"), size=TerminalSize(80, 20))
        )

    assert editor_tmux.created == []
    assert sessions.registrations == []


def test_document_target_helper_maps_kinds() -> None:
    assert document_target("plan", "a") == PlanDocument("a")
    with pytest.raises(ValueError, match="unsupported"):
        document_target("wiki", "a")
