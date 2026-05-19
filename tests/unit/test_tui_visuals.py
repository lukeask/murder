"""Small regression checks for TUI visual defaults."""

from __future__ import annotations

import sqlite3

import pytest

from murder.tui.app import MurderApp, _is_vim_style_quit
from murder.tui.chat_input import ChatInput
from murder.tui.planning_mode_widgets import ChatLog, NotesDocument, NotesList, PlanDocument, PlanList


def test_notes_document_is_focusable_for_tab_navigation() -> None:
    assert NotesDocument.can_focus is True
    assert NotesDocument().can_focus is True


def _binding_actions(widget_cls: type, key: str) -> list[str]:
    return [b.action for b in widget_cls._merged_bindings.key_to_bindings[key]]


def test_notes_document_line_keys_share_move_action() -> None:
    keys = {binding[0] for binding in NotesDocument.BINDINGS}
    assert {"j", "k", "up", "down", "pageup", "pagedown"} <= keys
    assert _binding_actions(NotesDocument, "down") == _binding_actions(NotesDocument, "j")
    assert _binding_actions(NotesDocument, "up") == _binding_actions(NotesDocument, "k")
    assert "scroll_down" not in _binding_actions(NotesDocument, "down")


def test_plan_document_line_keys_share_move_action() -> None:
    keys = {binding[0] for binding in PlanDocument.BINDINGS}
    assert {"j", "k", "up", "down", "pageup", "pagedown"} <= keys
    assert _binding_actions(PlanDocument, "down") == _binding_actions(PlanDocument, "j")
    assert _binding_actions(PlanDocument, "up") == _binding_actions(PlanDocument, "k")
    assert "scroll_down" not in _binding_actions(PlanDocument, "down")


def test_chat_log_arrow_matches_jk_actions() -> None:
    assert _binding_actions(ChatLog, "down") == _binding_actions(ChatLog, "j")
    assert _binding_actions(ChatLog, "up") == _binding_actions(ChatLog, "k")
    assert "scroll_down" not in _binding_actions(ChatLog, "down")


def test_chat_input_jk_matches_arrow_actions() -> None:
    assert _binding_actions(ChatInput, "j") == _binding_actions(ChatInput, "down")
    assert _binding_actions(ChatInput, "k") == _binding_actions(ChatInput, "up")


def test_plan_list_arrow_matches_jk_actions() -> None:
    assert _binding_actions(PlanList, "down") == _binding_actions(PlanList, "j")
    assert _binding_actions(PlanList, "up") == _binding_actions(PlanList, "k")


def test_notes_list_arrow_matches_jk_actions() -> None:
    assert _binding_actions(NotesList, "down") == _binding_actions(NotesList, "j")
    assert _binding_actions(NotesList, "up") == _binding_actions(NotesList, "k")


def test_notes_list_has_retire_confirmation_binding() -> None:
    assert _binding_actions(NotesList, "r") == ["retire_note"]


def test_notes_list_refresh_tolerates_legacy_notes_without_status(monkeypatch) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE notes (
            name TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            materialized_path TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO notes (name, created_at, updated_at, body, materialized_path)
        VALUES ('legacy', '2026-01-01T00:00:00', '2026-01-02T00:00:00',
                'body', '.murder/notes/legacy.md')
        """
    )
    notes = NotesList()
    rendered: list[tuple[str, str, str]] = []
    monkeypatch.setattr(notes, "clear", lambda *args, **kwargs: None)
    monkeypatch.setattr(notes, "add_row", lambda *cells, **kwargs: rendered.append(cells))
    monkeypatch.setattr(notes, "move_cursor", lambda *args, **kwargs: None)

    try:
        notes.refresh_from_db(conn)
    finally:
        conn.close()

    assert notes.selected_name == "legacy"
    assert rendered == [("legacy", "4", "2026-01-02 00:00")]


def test_chat_log_uses_flexible_width() -> None:
    assert "width: 1fr;" in ChatLog.DEFAULT_CSS


# ── pane motion spine (VISION §4.3) ────────────────────────────────────────


def _app_binding_actions(key: str) -> list[str]:
    return [b.action for b in MurderApp._merged_bindings.key_to_bindings.get(key, [])]


def test_app_binds_ctrl_hjkl_to_pane_focus() -> None:
    assert _app_binding_actions("ctrl+h") == ["focus_left"]
    assert _app_binding_actions("ctrl+j") == ["focus_down"]
    assert _app_binding_actions("ctrl+k") == ["focus_up"]
    assert _app_binding_actions("ctrl+l") == ["focus_right"]


def test_app_binds_ctrl_arrows_parallel_to_ctrl_hjkl() -> None:
    assert _app_binding_actions("ctrl+left") == _app_binding_actions("ctrl+h")
    assert _app_binding_actions("ctrl+down") == _app_binding_actions("ctrl+j")
    assert _app_binding_actions("ctrl+up") == _app_binding_actions("ctrl+k")
    assert _app_binding_actions("ctrl+right") == _app_binding_actions("ctrl+l")


def test_app_binds_tab_to_focus_traversal() -> None:
    assert _app_binding_actions("tab") == ["focus_next_region"]
    assert _app_binding_actions("shift+tab") == ["focus_previous_region"]


def test_app_binds_ctrl_verb_chords_for_common_actions() -> None:
    assert _app_binding_actions("ctrl+comma") == ["open_settings"]
    assert _app_binding_actions("ctrl+/") == ["show_help_force"]
    assert _app_binding_actions("question_mark") == ["show_help_force"]
    assert _app_binding_actions("ctrl+f") == ["focus_chat"]
    assert _app_binding_actions("ctrl+r") == ["refresh_now"]
    assert _app_binding_actions("ctrl+u") == ["collect_usage"]
    assert _app_binding_actions("f1") == []
    assert _app_binding_actions("f2") == []
    assert _app_binding_actions("r") == []
    assert _app_binding_actions("u") == []


def test_tab_binding_is_priority_so_textarea_cannot_swallow_it() -> None:
    bindings = MurderApp._merged_bindings.key_to_bindings["tab"]
    assert any(b.priority for b in bindings)


def test_ctrl_hjkl_bindings_are_priority() -> None:
    """ctrl+hjkl must be priority=True so they fire even when ChatInput
    (a TextArea) is focused.  TextArea owns ctrl+left/right (word nav) and
    ctrl+k (delete-to-EOL); without priority=True it consumes those keys
    before the App sees them, making pane navigation impossible from chat."""
    for key in ("ctrl+h", "ctrl+j", "ctrl+k", "ctrl+l"):
        bindings = MurderApp._merged_bindings.key_to_bindings.get(key, [])
        assert bindings, f"no binding found for {key}"
        assert any(b.priority for b in bindings), (
            f"binding for {key!r} must have priority=True so TextArea cannot swallow it"
        )


def test_ctrl_arrow_bindings_are_priority() -> None:
    """ctrl+arrow aliases: ctrl+left/right are bound by TextArea for word-motion,
    so the App's pane-navigation must win."""
    for key in ("ctrl+left", "ctrl+down", "ctrl+up", "ctrl+right"):
        bindings = MurderApp._merged_bindings.key_to_bindings.get(key, [])
        assert bindings, f"no binding found for {key}"
        assert any(b.priority for b in bindings), (
            f"binding for {key!r} must have priority=True so TextArea cannot swallow it"
        )


def test_bare_hjkl_are_not_app_level_so_widgets_keep_intra_pane_motion() -> None:
    # Sanity: the app must not steal directional keys; only ctrl-modified
    # variants and tab move focus between panes.
    for key in ("h", "j", "k", "l"):
        assert _app_binding_actions(key) == [], (
            f"App binds bare {key!r}; that would break intra-pane vim motion"
        )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (":wq", True),
        (":WQ", True),
        ("  :wq  ", True),
        (":wq\n", True),
        (":q!", True),
        (":Q!", True),
        ("  :q!  ", True),
        (":q!\nignored next line", False),
        ("so I was thinking about :wq and :q! the other day", False),
        (":wq trailing junk", False),
        ("junk\n:wq", False),
        (":x", False),
        (":q", False),
        (":!", False),
    ],
)
def test_is_vim_style_quit_requires_whole_message(text: str, expected: bool) -> None:
    assert _is_vim_style_quit(text) is expected


@pytest.mark.asyncio
async def test_notes_document_show_skips_update_when_identity_unchanged(monkeypatch):
    doc = NotesDocument()
    payloads: list[str] = []

    async def spy_update(markdown: str) -> None:
        payloads.append(markdown)

    monkeypatch.setattr(doc, "update", spy_update)
    await doc.show("2026-05-01", "## hi")
    await doc.show("2026-05-01", "## hi")
    await doc.show("2026-05-01", "  ## hi\n")
    await doc.show("2026-05-02", "## hi")
    assert payloads == ["## hi", "## hi"]


@pytest.mark.asyncio
async def test_plan_document_set_plan_markdown_skips_update_when_unchanged(monkeypatch):
    doc = PlanDocument()
    payloads: list[str] = []

    async def spy_update(markdown: str) -> None:
        payloads.append(markdown)

    monkeypatch.setattr(doc, "update", spy_update)
    await doc.set_plan_markdown("p1", "# one")
    await doc.set_plan_markdown("p1", "# one")
    await doc.set_plan_markdown("p2", "# one")
    assert payloads == ["# one", "# one"]
    assert doc.border_title == "p2"
