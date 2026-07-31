"""Prompt-template configuration compatibility and name validation."""

from __future__ import annotations

from pathlib import Path

from murder.user_config import (
    load_prompt_templates,
    load_templates,
    prompt_templates_path,
    save_prompt_templates,
    save_templates,
    templates_path,
)


def test_prompt_template_names_allow_human_readable_names_and_keep_legacy_aliases(
    tmp_path: Path,
) -> None:
    path = tmp_path / "templates.yaml"
    saved = save_prompt_templates(
        [
            {"name": "Review Context", "body": "review"},
            {"name": "release-checklist_2", "body": "ship"},
            {"name": "two  spaces", "body": "invalid"},
            {"name": " leading", "body": "invalid"},
            {"name": "bad:colon", "body": "invalid"},
            {"name": 'bad "quote"', "body": "invalid"},
        ],
        path,
    )

    assert saved == [
        {"name": "Review Context", "body": "review"},
        {"name": "release-checklist_2", "body": "ship"},
    ]
    assert load_prompt_templates(path) == saved
    assert load_templates(path) == saved
    assert save_templates(saved, path) == saved
    assert templates_path() == prompt_templates_path()
    assert "templates:\n" in path.read_text(encoding="utf-8")
