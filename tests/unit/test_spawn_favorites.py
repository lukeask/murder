"""Tests for murder.user_config spawn-favorites persistence — the on-disk CONTRACT
the spawn wizard's load/save RPCs depend on.

EDGE CASES only: a happy save→load round-trip is self-evident from the code, so we
fold it into the one test that also pins the normalization contract (blank-name drop
+ clamp-to-10), and we pin the real failure mode (malformed file must never raise).
All paths are hermetic via an explicit `path=` (no `~/.config`).
"""

from __future__ import annotations

from pathlib import Path

from murder.user_config import load_spawn_favorites, save_spawn_favorites


def test_save_load_round_trip_drops_blank_names_and_clamps_to_ten(tmp_path: Path) -> None:
    fav_path = tmp_path / "spawn_favorites.yaml"
    # 11 named records + 1 blank-name record (12 total). The blank one is dropped,
    # leaving 11 valid records, which is then clamped to the first 10 — in order.
    records = [
        {"name": f"fav-{i}", "harness": "codex", "model": "gpt-5.5", "effort": "high"}
        for i in range(11)
    ]
    records.insert(3, {"name": "   ", "harness": "codex", "model": "m", "effort": "low"})

    save_spawn_favorites(records, path=fav_path)
    loaded = load_spawn_favorites(path=fav_path)

    assert [r["name"] for r in loaded] == [f"fav-{i}" for i in range(10)]
    # The first record round-tripped every field verbatim.
    assert loaded[0] == {
        "name": "fav-0",
        "harness": "codex",
        "model": "gpt-5.5",
        "effort": "high",
    }


def test_load_on_malformed_file_returns_empty_never_raises(tmp_path: Path) -> None:
    fav_path = tmp_path / "spawn_favorites.yaml"
    # A non-dict top-level YAML document (a bare list) — the real corruption mode.
    fav_path.write_text("- just\n- a\n- list\n", encoding="utf-8")

    assert load_spawn_favorites(path=fav_path) == []
