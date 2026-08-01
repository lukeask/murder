"""Explicit test evidence: imports and calls the production unit."""

from widget import widget_save


def test_widget_save():
    assert widget_save({"ok": True}) == {"ok": True}
