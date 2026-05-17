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


@pytest.mark.asyncio
async def test_note_capture_enter_submit() -> None:
    closed: list[tuple[bool, str]] = []

    def on_done(payload: tuple[bool, str] | None) -> None:
        if payload is not None:
            closed.append(payload)

    app = Shell()
    async with app.run_test() as pilot:
        app.push_screen(
            NoteCaptureScreen(
                initial_draft="",
                load_recent_rows=list,
            ),
            on_done,
        )
        await pilot.pause()
        await pilot.pause()
        draft = app.screen.query_one("#draft", NoteCaptureScreen.Draft)
        assert draft.has_focus
        await pilot.press(*list("buy milk"))
        await pilot.press("enter")
        await pilot.pause()
        assert closed == [(True, "buy milk")]
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
            )
        )
        await asyncio.sleep(0.08)
        table = app.screen.query_one("#recent_table", RecentNotesTable)
        assert table.row_count == len(rows_data)


@pytest.mark.asyncio
async def test_slash_note_with_body_submits(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    captured: list[str] = []

    def fake_record(self: MurderApp, text: str) -> dict[str, object]:
        captured.append(text)
        return {"cleaned": text, "entry_id": 1, "note_name": "n"}

    def fake_run_worker(self: MurderApp, coro, **kwargs):  # type: ignore[no-untyped-def]
        del self, kwargs
        coro.close()

    monkeypatch.setattr(MurderApp, "_record_note_capture_immediate", fake_record)
    monkeypatch.setattr(MurderApp, "run_worker", fake_run_worker)

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


def test_record_note_capture_immediate_writes_timestamp_note(
    memdb: sqlite3.Connection,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            project=SimpleNamespace(name="test"),
            tui=SimpleNamespace(refresh_ms=1000),
        ),
        repo_root=tmp_path,
        db=memdb,
    )
    app = MurderApp(runtime)  # type: ignore[arg-type]

    def fake_refresh() -> None:
        return None

    def fake_run_worker(coro, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        coro.close()

    monkeypatch.setattr(app, "_refresh_db_views", fake_refresh)
    monkeypatch.setattr(app, "run_worker", fake_run_worker)
    created = app._record_note_capture_immediate("  durable thought  ")

    assert created is not None
    note_name = str(created["note_name"])
    assert (tmp_path / ".murder" / "notes" / f"{note_name}.md").read_text(
        encoding="utf-8"
    ) == "durable thought\n"
    assert dbmod.get_note(memdb, note_name) is not None


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
