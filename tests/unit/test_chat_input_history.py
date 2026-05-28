"""Sent-message recall for the chat input box."""

from __future__ import annotations

from murder.tui.chat_input import _SentMessageHistory


def test_empty_history_is_noop() -> None:
    hist = _SentMessageHistory()
    assert hist.browse_up("draft") is None
    assert hist.browse_down() is None


def test_up_down_walks_sent_messages_newest_first() -> None:
    hist = _SentMessageHistory()
    for msg in ("A", "B", "C"):
        hist.append(msg)
    assert hist.browse_up("") == "C"
    assert hist.browse_up("C") == "B"
    assert hist.browse_up("B") == "A"
    assert hist.browse_up("A") == "A"
    assert hist.browse_down() == "B"
    assert hist.browse_down() == "C"
    assert hist.browse_down() == ""


def test_draft_restored_after_browsing() -> None:
    hist = _SentMessageHistory()
    hist.append("sent")
    assert hist.browse_up("start of string") == "sent"
    assert hist.browse_down() == "start of string"


def test_up_up_down_returns_to_newest_not_oldest() -> None:
    hist = _SentMessageHistory()
    for msg in ("A", "B", "C"):
        hist.append(msg)
    hist.browse_up("")
    hist.browse_up("C")
    assert hist.browse_down() == "C"


def test_append_resets_browse_position() -> None:
    hist = _SentMessageHistory()
    hist.append("first")
    hist.browse_up("")
    hist.append("second")
    assert hist.browse_up("") == "second"
