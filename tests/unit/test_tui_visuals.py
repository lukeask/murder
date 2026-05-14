"""Small regression checks for TUI visual defaults."""

from __future__ import annotations

from murder.tui.app import _chat_target_label
from murder.tui.chat_input import ChatInput
from murder.tui.plan_view import ChatLog, NotesDocument, NotesList, PlanDocument, PlanList


def test_chat_target_label_tracks_notetaker_mode() -> None:
    assert _chat_target_label("planning", "notetaker") == "notetaker"


def test_chat_target_label_defaults_to_collaborator() -> None:
    assert _chat_target_label("planning", "collaborator") == "collaborator"
    assert _chat_target_label("crows", "notetaker") == "collaborator"


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


def test_chat_log_uses_flexible_width() -> None:
    assert "width: 1fr;" in ChatLog.DEFAULT_CSS

