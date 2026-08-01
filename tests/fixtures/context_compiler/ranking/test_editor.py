"""Focused test for ProfileEditor.save — implementation should surface this."""

from editor import ProfileEditor


def test_save_profile():
    editor = ProfileEditor()
    assert editor.save({"name": "Ada"})["saved"]["name"] == "Ada"


def test_unrelated_helper():
    assert True
