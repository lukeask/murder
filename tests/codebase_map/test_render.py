"""Tests for the on-disk map renderer (t059)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from murder.codebase_map.render import (
    render_dir_summary,
    render_file_summary,
    render_root,
)
from murder.codebase_map.summarize import FileSummary


def _summary(path: str) -> FileSummary:
    return FileSummary(
        path=path,
        body="# the summary body",
        source_hash="a" * 64,
        source_tokens=100,
        summary_tokens=4,
    )


def test_render_file_mirrors_path_with_extension():
    with tempfile.TemporaryDirectory() as d:
        map_root = Path(d)
        render_file_summary(map_root, "murder/llm/foo.py", _summary("murder/llm/foo.py"))
        target = map_root / "murder" / "llm" / "foo.py.md"
        assert target.exists()
        text = target.read_text()
        assert "source_path: murder/llm/foo.py" in text
        assert f"source_hash: {'a' * 64}" in text
        assert "source_tokens: 100" in text
        assert "summary_tokens: 4" in text
        assert "generated_at:" in text
        assert "# the summary body" in text


def test_render_keeps_source_extension_distinct():
    with tempfile.TemporaryDirectory() as d:
        map_root = Path(d)
        render_file_summary(map_root, "a/foo.py", _summary("a/foo.py"))
        render_file_summary(map_root, "a/foo.pyi", _summary("a/foo.pyi"))
        assert (map_root / "a" / "foo.py.md").exists()
        assert (map_root / "a" / "foo.pyi.md").exists()


def test_render_dir_summary_writes_dir_md():
    with tempfile.TemporaryDirectory() as d:
        map_root = Path(d)
        render_dir_summary(map_root, "murder/llm", "dir body here")
        target = map_root / "murder" / "llm" / "DIR.md"
        assert target.exists()
        text = target.read_text()
        assert "dir body here" in text
        assert "source_path: murder/llm" in text
        # dir rollups null the source fields.
        assert "source_hash:\n" in text or text.rstrip().count("source_hash:") == 1


def test_render_root_writes_root_md():
    with tempfile.TemporaryDirectory() as d:
        map_root = Path(d)
        render_root(map_root, "root body here")
        target = map_root / "ROOT.md"
        assert target.exists()
        assert "root body here" in target.read_text()


def test_atomic_write_leaves_no_tmp():
    with tempfile.TemporaryDirectory() as d:
        map_root = Path(d)
        render_file_summary(map_root, "x.py", _summary("x.py"))
        tmps = list(map_root.rglob("*.tmp"))
        assert tmps == []
