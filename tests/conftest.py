from __future__ import annotations

from pathlib import Path

import pytest

from murder.runtime.terminal import tmux as tmux_mod
from murder.state.persistence.connection import RepoDb
from tests.support.database import (
    SECOND_TEST_REPOSITORY_ID,
    TEST_REPOSITORY_ID,
    open_test_repo_db,
)
from tests.support.fake_tmux import FakeTmux


@pytest.fixture(autouse=True)
def isolated_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep the consolidated user database and config isolated per test."""
    root = tmp_path / "xdg-config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "xdg-runtime"))
    return root


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


@pytest.fixture
def repo_db(tmp_path: Path) -> RepoDb:
    """Initialized Turso database scoped to a deterministic repository."""
    db = open_test_repo_db(tmp_path / "murder.db")
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def second_repo_db(tmp_path: Path) -> RepoDb:
    """A second partition in the same test-owned shared Turso database."""
    db = open_test_repo_db(
        tmp_path / "murder.db", repository_id=SECOND_TEST_REPOSITORY_ID
    )
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def test_repository_id() -> str:
    return TEST_REPOSITORY_ID
