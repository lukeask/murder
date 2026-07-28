from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from murder.app.service.document_access import DocumentAccess
from murder.app.service.document_editor_sessions import DocumentEditorSessions


class FakeEditorTmux:
    def __init__(self) -> None:
        self.sessions: set[str] = set()
        self.created: list[tuple[str, Path, list[str], int, int]] = []
        self.resized: list[tuple[str, int, int]] = []
        self.sent: list[tuple[str, str, bool, bool]] = []
        self.frame = "\x1b[31mvim frame\x1b[0m"
        self.dimensions = (91, 27)

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

    async def send_keys(
        self, name: str, text: str, *, literal: bool = True, enter: bool = True
    ) -> None:
        self.sent.append((name, text, literal, enter))

    async def capture_viewport(self, name: str, *, escapes: bool = False) -> str:
        assert name in self.sessions
        assert escapes is True
        return self.frame

    async def pane_dimensions(self, name: str) -> tuple[int, int]:
        assert name in self.sessions
        return self.dimensions


@pytest.fixture
def editor_tmux(monkeypatch: pytest.MonkeyPatch) -> FakeEditorTmux:
    fake = FakeEditorTmux()
    monkeypatch.setattr(
        "murder.app.service.document_editor_sessions.tmux.session_exists",
        fake.session_exists,
    )
    monkeypatch.setattr(
        "murder.app.service.document_editor_sessions.tmux.create_session",
        fake.create_session,
    )
    monkeypatch.setattr(
        "murder.app.service.document_editor_sessions.tmux.resize_session",
        fake.resize_session,
    )
    monkeypatch.setattr(
        "murder.app.service.document_editor_sessions.tmux.send_keys",
        fake.send_keys,
    )
    monkeypatch.setattr(
        "murder.app.service.document_editor_sessions.tmux.capture_viewport",
        fake.capture_viewport,
    )
    monkeypatch.setattr(
        "murder.app.service.document_editor_sessions.tmux.pane_dimensions",
        fake.pane_dimensions,
    )
    return fake


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
    sessions = DocumentEditorSessions(repo, DocumentAccess(repo))

    session, reused = await sessions.start("plan", "plan with spaces", columns=73, rows=19)

    assert reused is False
    assert session.document_path == document.resolve()
    assert session.tmux_name.startswith("murder_editor_")
    assert str(document) not in session.tmux_name
    assert editor_tmux.created == [
        (
            session.tmux_name,
            repo.resolve(),
            ["/usr/bin/env", "vim", "--clean", str(document.resolve())],
            73,
            19,
        )
    ]


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
    sessions = DocumentEditorSessions(repo, DocumentAccess(repo))

    session, reused = await sessions.start("plan", "safe", columns=80, rows=20)

    assert reused is False
    assert editor_tmux.created[0][2] == ["/bin/true", "--fallback", str(session.document_path)]


async def test_concurrent_start_reuses_one_session_and_drives_only_that_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    editor_tmux: FakeEditorTmux,
) -> None:
    repo = tmp_path / "repo"
    document = repo / ".murder" / "plans" / "safe.md"
    document.parent.mkdir(parents=True)
    document.write_text("# safe\n")
    monkeypatch.setenv("VISUAL", "/bin/true")
    sessions = DocumentEditorSessions(repo, DocumentAccess(repo))

    first, second = await asyncio.gather(
        sessions.start("plan", "safe", columns=80, rows=20),
        sessions.start("plan", "safe", columns=90, rows=25),
    )

    first_session, first_reused = first
    second_session, second_reused = second
    assert first_session == second_session
    assert (first_reused, second_reused) == (False, True)
    assert len(editor_tmux.created) == 1
    assert editor_tmux.resized == [(first_session.tmux_name, 90, 25)]

    await sessions.send(first_session.session_id, "Escape", literal=False)
    await sessions.resize(first_session.session_id, columns=91, rows=27)
    frame = await sessions.capture(first_session.session_id)

    assert editor_tmux.sent == [(first_session.tmux_name, "Escape", False, False)]
    assert editor_tmux.resized[-1] == (first_session.tmux_name, 91, 27)
    assert frame is not None
    assert frame.data == editor_tmux.frame
    assert (frame.columns, frame.rows) == editor_tmux.dimensions


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
    sessions = DocumentEditorSessions(repo, DocumentAccess(repo))

    with pytest.raises(ValueError, match="outside the repository"):
        await sessions.start("plan", "escape", columns=80, rows=20)

    assert editor_tmux.created == []


@pytest.mark.parametrize(
    ("visual", "editor", "message"),
    [
        (None, None, "no editor configured"),
        ("definitely-not-a-real-editor --flag", "/bin/true", "executable was not found"),
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
    sessions = DocumentEditorSessions(repo, DocumentAccess(repo))

    with pytest.raises(RuntimeError, match=message):
        await sessions.start("plan", "safe", columns=80, rows=20)

    assert editor_tmux.created == []
