"""Quick-capture overlay — ESC layering, chords, Enter submit, /note."""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest
from textual.app import App
from textual.widgets import Static

from murder import db as dbmod
from murder.tui.app import MurderApp
from murder.tui.note_capture import RECENT_NOTE_ROWS, NoteCaptureScreen, RecentNotesTable


class Shell(App):
    """Minimal stack under the modal."""

    def compose(self):
        yield Static("under")


async def _reject_submit(_: str) -> bool:
    raise AssertionError("submit should not run")


@pytest.mark.asyncio
async def test_note_capture_enter_submit() -> None:
    submitted: list[str] = []

    async def submit_capture(text: str) -> bool:
        submitted.append(text)
        return True

    app = Shell()
    async with app.run_test() as pilot:
        app.push_screen(
            NoteCaptureScreen(
                initial_draft="",
                load_recent_rows=list,
                submit_capture=submit_capture,
            )
        )
        await pilot.pause()
        await pilot.pause()
        draft = app.screen.query_one("#draft", NoteCaptureScreen.Draft)
        assert draft.has_focus
        await pilot.press(*list("buy milk"))
        await pilot.press("enter")
        await asyncio.sleep(0.15)
        assert submitted == ["buy milk"]
        assert not isinstance(app.screen, NoteCaptureScreen)


@pytest.mark.asyncio
async def test_note_capture_esc_esc_closes_preserving_draft() -> None:
    closed: list[tuple[bool, str]] = []

    def on_done(payload: tuple[bool, str] | None) -> None:
        if payload is not None:
            closed.append(payload)

    app = Shell()
    async with app.run_test() as pilot:
        app.push_screen(
            NoteCaptureScreen(
                initial_draft="wip thought",
                load_recent_rows=list,
                submit_capture=_reject_submit,
            ),
            on_done,
        )
        await asyncio.sleep(0.02)
        await pilot.press("escape")
        await pilot.press("escape")
        await asyncio.sleep(0.05)
        assert closed == [(False, "wip thought")]
        assert not isinstance(app.screen, NoteCaptureScreen)


@pytest.mark.asyncio
async def test_note_capture_esc_then_blur_then_esc_from_list() -> None:
    app = Shell()
    async with app.run_test() as pilot:
        app.push_screen(
            NoteCaptureScreen(
                initial_draft="x",
                load_recent_rows=lambda: [{"short_vers": "one", "cleaned": "full one"}],
                submit_capture=_reject_submit,
            )
        )
        await asyncio.sleep(0.08)
        await pilot.press("escape")
        await asyncio.sleep(0.4)
        table = app.screen.query_one("#recent_table", RecentNotesTable)
        assert table.display and app.screen.focused is table
        await pilot.press("escape")
        await asyncio.sleep(0.05)
        assert not isinstance(app.screen, NoteCaptureScreen)


@pytest.mark.asyncio
async def test_note_capture_escape_d_chord_and_undo() -> None:
    app = Shell()
    async with app.run_test() as pilot:
        app.push_screen(
            NoteCaptureScreen(
                initial_draft="",
                load_recent_rows=list,
                submit_capture=_reject_submit,
            )
        )
        await asyncio.sleep(0.02)
        await pilot.press(*list("abc"))
        await pilot.press("escape")
        await pilot.press("d")
        draft = app.screen.query_one("#draft", NoteCaptureScreen.Draft)
        assert draft.text == ""
        await pilot.press("u")
        assert draft.text == "abc"


@pytest.mark.asyncio
async def test_recent_rows_populate_table() -> None:
    rows_data = [
        {"short_vers": "alpha line", "cleaned": "Alpha cleaned body"},
        {"short_vers": "beta line", "cleaned": "Beta cleaned body"},
    ]

    app = Shell()
    async with app.run_test():
        app.push_screen(
            NoteCaptureScreen(
                initial_draft="",
                load_recent_rows=lambda: rows_data,
                submit_capture=_reject_submit,
            )
        )
        await asyncio.sleep(0.08)
        table = app.screen.query_one("#recent_table", RecentNotesTable)
        assert table.row_count == len(rows_data)


@pytest.mark.asyncio
async def test_slash_note_with_body_submits(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    captured: list[str] = []

    async def fake_submit(self: MurderApp, text: str) -> bool:
        captured.append(text)
        return True

    monkeypatch.setattr(MurderApp, "_submit_note_capture_async", fake_submit)

    runtime = SimpleNamespace(
        config=SimpleNamespace(
            project=SimpleNamespace(name="test"),
            tui=SimpleNamespace(refresh_ms=1000),
        ),
        repo_root=tmp_path,
        db=None,
    )
    app = MurderApp(runtime)  # type: ignore[arg-type]
    await app._dispatch_chat("/note hello world")
    await asyncio.sleep(0.08)
    assert captured == ["hello world"]


@pytest.mark.asyncio
async def test_slash_note_bare_opens_overlay(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    opens: list[bool] = []

    def fake_open(self: MurderApp) -> None:
        opens.append(True)

    monkeypatch.setattr(MurderApp, "action_open_note_capture", fake_open)

    runtime = SimpleNamespace(
        config=SimpleNamespace(
            project=SimpleNamespace(name="test"),
            tui=SimpleNamespace(refresh_ms=1000),
        ),
        repo_root=tmp_path,
        db=None,
    )
    app = MurderApp(runtime)  # type: ignore[arg-type]
    await app._dispatch_chat("/note")
    assert opens == [True]


def test_murder_app_binds_ctrl_n_for_quick_capture(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            project=SimpleNamespace(name="test"),
            tui=SimpleNamespace(refresh_ms=1000),
        ),
        repo_root=tmp_path,
        db=None,
    )
    app = MurderApp(runtime)  # type: ignore[arg-type]

    def _binding_key(b):
        return b.key if hasattr(b, "key") else b[0]

    def _binding_action(b):
        return b.action if hasattr(b, "action") else b[1]

    assert any(
        _binding_key(b) == "ctrl+n" and _binding_action(b) == "open_note_capture"
        for b in app.BINDINGS
    )


def test_sync_recent_note_entries_respects_twelve_limit(
    memdb: sqlite3.Connection,
    tmp_path,
) -> None:
    for i in range(15):
        dbmod.insert_notes_entry(memdb, raw=f"r{i}", cleaned=f"c{i}", short_vers=f"s{i}")

    runtime = SimpleNamespace(
        config=SimpleNamespace(
            project=SimpleNamespace(name="test"),
            tui=SimpleNamespace(refresh_ms=1000),
        ),
        repo_root=tmp_path,
        db=memdb,
    )
    app = MurderApp(runtime)  # type: ignore[arg-type]
    rows = app._sync_recent_note_entries()
    assert len(rows) == RECENT_NOTE_ROWS
