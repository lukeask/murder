"""Tests for murder.user_config theme persistence — the on-disk CONTRACT the
theme RPCs and init/daemon seeding depend on.

Cookbook cases first (seed, round-trip, import); edge cases after (malformed file,
builtin protection, duplicate id).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from murder.user_config import (
    _PALETTE_SLOTS,
    ensure_user_themes,
    format_theme_from_json,
    import_theme_from_json,
    load_builtin_theme_jsons,
    load_themes,
    save_themes,
)


def _sample_palette() -> dict[str, str]:
    everforest = next(t for t in load_builtin_theme_jsons() if t["id"] == "everforest-dark")
    return dict(everforest["palette"])


def test_bundled_theme_jsons_validate_and_cover_shortlist() -> None:
    builtins = load_builtin_theme_jsons()
    ids = {t["id"] for t in builtins}
    assert {
        "everforest-dark",
        "everforest-light",
        "tokyo-night",
        "dracula",
        "gruvbox-dark",
        "catppuccin-mocha",
        "nord",
        "one-dark",
    } <= ids
    for raw in builtins:
        assert raw["variant"] in ("light", "dark")
        assert isinstance(raw["palette"], dict)
        assert len(raw["palette"]) == len(_PALETTE_SLOTS)


def test_ensure_user_themes_seeds_missing_builtins(tmp_path: Path) -> None:
    themes_file = tmp_path / "themes.yaml"
    assert not themes_file.exists()

    changed = ensure_user_themes(path=themes_file)
    assert changed is True

    loaded = load_themes(path=themes_file)
    assert len(loaded) == len(load_builtin_theme_jsons())
    assert all(rec["builtin"] is True for rec in loaded)
    assert {rec["id"] for rec in loaded} == {t["id"] for t in load_builtin_theme_jsons()}

    # Second call is a no-op when everything is already present.
    assert ensure_user_themes(path=themes_file) is False


def test_save_load_round_trip_preserves_custom_theme(tmp_path: Path) -> None:
    themes_file = tmp_path / "themes.yaml"
    ensure_user_themes(path=themes_file)

    custom = {
        "id": "my-theme",
        "name": "My Theme",
        "variant": "dark",
        "builtin": False,
        "palette": _sample_palette(),
    }
    existing = load_themes(path=themes_file)
    save_themes(existing + [custom], path=themes_file)
    loaded = load_themes(path=themes_file)

    by_id = {rec["id"]: rec for rec in loaded}
    assert by_id["my-theme"]["name"] == "My Theme"
    assert by_id["my-theme"]["builtin"] is False
    assert by_id["everforest-dark"]["builtin"] is True


def test_import_theme_from_json_appends_custom_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    themes_file = tmp_path / "themes.yaml"
    monkeypatch.setattr("murder.user_config.themes_path", lambda path=None: themes_file)
    ensure_user_themes(path=themes_file)

    wrapper = {
        "id": "paste-theme",
        "name": "Paste Theme",
        "variant": "dark",
        "palette": _sample_palette(),
    }
    themes, new_id = import_theme_from_json(json.dumps(wrapper))
    assert new_id == "paste-theme"
    assert any(t["id"] == "paste-theme" and t["builtin"] is False for t in themes)


def test_format_theme_from_json_rejects_duplicate_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    themes_file = tmp_path / "themes.yaml"
    monkeypatch.setattr("murder.user_config.themes_path", lambda path=None: themes_file)
    ensure_user_themes(path=themes_file)

    wrapper = {
        "id": "everforest-dark",
        "name": "Collision",
        "variant": "dark",
        "palette": _sample_palette(),
    }
    with pytest.raises(ValueError, match="already exists"):
        format_theme_from_json(json.dumps(wrapper))


def test_save_themes_reinjects_removed_builtins(tmp_path: Path) -> None:
    themes_file = tmp_path / "themes.yaml"
    ensure_user_themes(path=themes_file)
    custom_only = [
        {
            "id": "solo-custom",
            "name": "Solo",
            "variant": "dark",
            "builtin": False,
            "palette": _sample_palette(),
        }
    ]
    saved = save_themes(custom_only, path=themes_file)
    ids = {rec["id"] for rec in saved}
    assert "solo-custom" in ids
    assert "everforest-dark" in ids


def test_load_on_malformed_file_returns_empty_never_raises(tmp_path: Path) -> None:
    themes_file = tmp_path / "themes.yaml"
    themes_file.write_text("- not\n- a\n- dict\n", encoding="utf-8")
    assert load_themes(path=themes_file) == []
