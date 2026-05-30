from __future__ import annotations

from pathlib import Path

from murder.enforcement.watcher import _is_allowed_write


def test_is_allowed_write_accepts_descendants_of_allowed_directory() -> None:
    assert _is_allowed_write(Path("src/module.py"), {Path("src")})
    assert not _is_allowed_write(Path("other/module.py"), {Path("src")})
