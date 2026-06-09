from __future__ import annotations

from pathlib import Path

import pytest

from murder.llm.harnesses.model_cache import clear_model_cache
from murder.runtime.terminal import tmux as tmux_mod
from tests.support.fake_tmux import FakeTmux


@pytest.fixture(autouse=True)
def _reset_model_cache():
    """Ensure the module-global model cache is clean before and after every test.

    Several tests (e.g. test_orchestrator_worktrees) call code paths that now
    trigger live harness discovery as a side-effect.  Without this guard the
    first real-tmux probe can pollute _CACHE with tmux setup strings, causing
    unrelated tests (e.g. test_spawn_wizard) to see unexpected model lists.
    """
    clear_model_cache()
    yield
    clear_model_cache()


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
