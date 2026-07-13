from __future__ import annotations

from pathlib import Path

import pytest

from murder.runtime.terminal import tmux as tmux_mod
from tests.support.fake_tmux import FakeTmux


@pytest.fixture
def fake_tmux(monkeypatch):
    ft = FakeTmux()
    ft.install(monkeypatch, tmux_mod)

    async def _noop_sleep(_: float = 0) -> None:
        pass

    monkeypatch.setattr("asyncio.sleep", _noop_sleep)
    return ft


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    """Temporary repo root for filesystem-oriented tests."""
    root = tmp_path / "repo"
    root.mkdir()
    return root
