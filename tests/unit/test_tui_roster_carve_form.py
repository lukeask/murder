"""Helpers for the Dispatch ticket metadata (carve) form."""

from __future__ import annotations

import sqlite3

import pytest
from textual.app import App
from textual.widgets import Static

from murder.harnesses import REGISTRY
from murder.tui.dispatch.roster import (
    _HARNESS_SELECT_OPTIONS,
    _STATUS_SELECT_OPTIONS,
    CarveFormScreen,
    CarveTextArea,
    RadioRow,
    TitleStripInput,
    _model_select_options,
    _schedule_select_options,
    _wave_select_options,
)


def test_status_select_options_include_done_and_archived() -> None:
    vals = {v for _, v in _STATUS_SELECT_OPTIONS}
    assert "done" in vals
    assert "archived" in vals


def test_wave_select_options_include_current_and_db(memdb: sqlite3.Connection) -> None:
    memdb.execute(
        "INSERT INTO tickets(id, title, wave, status, harness, model, attempts, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("t001", "A", 2, "planned", "cursor", None, 0, "2026-01-01", "2026-01-01"),
    )
    opts = _wave_select_options(memdb, current=7)
    values = [v for _, v in opts]
    assert 0 in values and 2 in values and 7 in values


def test_schedule_select_options_keep_current() -> None:
    opts = _schedule_select_options("2026-06-01T12:00:00+00:00")
    vals = [v for _, v in opts]
    assert any(v == "2026-06-01T12:00:00+00:00" for v in vals)


def test_model_select_options_include_registry_and_unknown() -> None:
    kind = next(iter(REGISTRY.keys()))
    opts = _model_select_options(kind, "totally-unknown-model-xyz")
    ids = [v for _, v in opts]
    assert "" in ids
    assert "totally-unknown-model-xyz" in ids


def test_harness_options_cover_registry() -> None:
    kinds = {v for _, v in _HARNESS_SELECT_OPTIONS}
    assert kinds == set(REGISTRY.keys())


def test_radio_row_respects_initial_value() -> None:
    row = RadioRow([("One", 1), ("Two", 2)], value=2, id="t")
    assert row.value == 2


def test_radio_row_set_options_updates_value() -> None:
    row = RadioRow([("a", "x")], value="x", id="t")
    row.set_options([("b", "y"), ("c", "z")], value="z")
    assert row.value == "z"


class _Shell(App):
    """Minimal app shell to host the CarveFormScreen modal."""

    def compose(self):
        yield Static("under")


def _carve_screen(db: sqlite3.Connection) -> CarveFormScreen:
    return CarveFormScreen(
        "t001",
        {"title": "Demo", "wave": 0, "status": "planned", "harness": "cursor"},
        harness_hint="cursor",
        db=db,
        on_autosave=lambda _spec: None,
    )


@pytest.mark.asyncio
async def test_j_on_title_input_browse_mode_shifts_focus(
    memdb: sqlite3.Connection,
) -> None:
    """j while a browse-mode TitleStripInput has focus must not trap."""
    app = _Shell()
    async with app.run_test() as pilot:
        app.push_screen(_carve_screen(memdb))
        await pilot.pause()
        await pilot.pause()
        title = app.screen.query_one("#field_title", TitleStripInput)
        title.focus()
        await pilot.pause()
        assert title.has_focus
        assert not title.editing
        await pilot.press("j")
        await pilot.pause()
        # Focus moved off the title; the j was navigation, not typed text.
        assert app.screen.focused is not title
        assert app.screen.focused is app.screen.query_one("#field_wave", RadioRow)
        assert title.value == "Demo"


@pytest.mark.asyncio
async def test_j_on_title_input_edit_mode_types(
    memdb: sqlite3.Connection,
) -> None:
    """In edit mode j must reach the Input as a typed character, not navigate."""
    app = _Shell()
    async with app.run_test() as pilot:
        app.push_screen(_carve_screen(memdb))
        await pilot.pause()
        await pilot.pause()
        title = app.screen.query_one("#field_title", TitleStripInput)
        title.focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert title.editing
        await pilot.press("j")
        await pilot.pause()
        assert title.has_focus
        assert "j" in title.value


@pytest.mark.asyncio
async def test_escape_on_title_input_edit_mode_returns_to_browse(
    memdb: sqlite3.Connection,
) -> None:
    """Escape exits TitleStripInput edit mode without closing the modal."""
    app = _Shell()
    async with app.run_test() as pilot:
        app.push_screen(_carve_screen(memdb))
        await pilot.pause()
        await pilot.pause()
        title = app.screen.query_one("#field_title", TitleStripInput)
        title.focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert title.editing
        await pilot.press("escape")
        await pilot.pause()
        assert not title.editing
        assert isinstance(app.screen, CarveFormScreen)


@pytest.mark.asyncio
async def test_j_on_textarea_browse_mode_shifts_focus(
    memdb: sqlite3.Connection,
) -> None:
    """j while a browse-mode CarveTextArea has focus must not trap."""
    app = _Shell()
    async with app.run_test() as pilot:
        app.push_screen(_carve_screen(memdb))
        await pilot.pause()
        await pilot.pause()
        extra = app.screen.query_one("#field_skills_extra", CarveTextArea)
        extra.focus()
        await pilot.pause()
        assert extra.has_focus
        assert not extra.editing
        await pilot.press("j")
        await pilot.pause()
        assert app.screen.focused is not extra
        assert app.screen.focused is app.screen.query_one("#field_writes", CarveTextArea)
