"""Phase 4: ProcessScope owns flock/DB/run/log/signal/advanced-log lifetime."""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from murder.app.service.process_scope import ProcessScope
from murder.config import (
    Config,
    CrowHandlerConfig,
    HarnessRoleConfig,
    ProjectConfig,
)
from murder.observability.advanced_log import NullAdvancedLog, current_advanced_log
from murder.state.storage.filesystem import lock_is_held
from murder.state.storage.paths import lock_path


def _config() -> Config:
    return Config(
        project=ProjectConfig(name="repo"),
        collaborator=HarnessRoleConfig(harness="codex"),
        default_crow=HarnessRoleConfig(harness="codex"),
        crow_handler=CrowHandlerConfig(model="test-model"),
    )


def test_acquisition_success(repo_root: Path) -> None:
    async def _drive() -> tuple[str, object]:
        async with ProcessScope.open(_config(), repo_root) as scope:
            assert scope.db is not None
            assert scope.run_id
            assert scope.events is not None
            assert scope.commands is not None
            assert scope.advanced_log is not None
            assert lock_is_held(lock_path(repo_root))
            return scope.run_id, scope.resources

    run_id, resources = asyncio.run(_drive())
    assert run_id
    assert resources.db is not None
    assert lock_is_held(lock_path(repo_root)) is False


def test_failure_after_flock_releases_flock(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "murder.app.service.process_scope.open_repo_db",
        lambda _root: (_ for _ in ()).throw(RuntimeError("db boom")),
    )
    lock = lock_path(repo_root)

    async def _drive() -> None:
        with pytest.raises(RuntimeError, match="db boom"):
            async with ProcessScope.open(_config(), repo_root):
                pass

    asyncio.run(_drive())
    assert lock_is_held(lock) is False
    assert not lock.exists()


def test_failure_after_db_closes_db_and_releases_flock(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[str] = []

    from murder.state.persistence.connection import open_repo_db as real_open_repo_db

    def _open(root: Path):
        db = real_open_repo_db(root)
        original_close = db.close

        def _close() -> None:
            closed.append("db")
            original_close()

        object.__setattr__(db, "close", _close)
        return db

    monkeypatch.setattr("murder.app.service.process_scope.open_repo_db", _open)
    monkeypatch.setattr(
        "murder.app.service.process_scope.allocate_run_id",
        lambda _root: (_ for _ in ()).throw(RuntimeError("run boom")),
    )

    async def _drive() -> None:
        with pytest.raises(RuntimeError, match="run boom"):
            async with ProcessScope.open(_config(), repo_root):
                pass

    asyncio.run(_drive())
    assert closed == ["db"]
    assert lock_is_held(lock_path(repo_root)) is False


def test_failure_after_advanced_log_start_stops_it(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stopped = asyncio.Event()

    class _Adv:
        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            stopped.set()

        def write_session_info(self, **_kwargs) -> None:
            return None

        def record_artifact_ref(self, _record) -> None:
            return None

        def record_orchestration_event(self, _event) -> None:
            return None

    monkeypatch.setattr(
        "murder.app.service.process_scope.open_advanced_log",
        lambda *_a, **_k: _Adv(),
    )
    monkeypatch.setattr(
        "murder.app.service.process_scope.resolve_recorder_mode",
        lambda: "off",
    )
    monkeypatch.setattr(
        "murder.app.service.process_scope.PersistingCommandSubmitter",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("commands boom")),
    )

    async def _drive() -> None:
        with pytest.raises(RuntimeError, match="commands boom"):
            async with ProcessScope.open(_config(), repo_root):
                pass

    asyncio.run(_drive())
    assert stopped.is_set()


def test_normal_close_ends_run_before_db_close(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []

    from murder.state.persistence.connection import open_repo_db as real_open

    def _open(root: Path):
        db = real_open(root)
        original = db.close

        def _close() -> None:
            order.append("db.close")
            original()

        object.__setattr__(db, "close", _close)
        return db

    def _end_run(db, run_id) -> None:  # noqa: ANN001
        del db, run_id
        order.append("end_run")

    monkeypatch.setattr("murder.app.service.process_scope.open_repo_db", _open)
    monkeypatch.setattr("murder.app.service.process_scope._db_end_run", _end_run)

    async def _drive() -> None:
        async with ProcessScope.open(_config(), repo_root):
            pass

    asyncio.run(_drive())
    assert order == ["end_run", "db.close"]


def test_recorder_subscription_canceled_before_advanced_log_stop(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []

    class _Sub:
        def cancel(self) -> None:
            order.append("recorder.cancel")

    class _Events:
        def subscribe(self, _handler):
            return _Sub()

    class _Adv:
        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            order.append("advanced_log.stop")

        def write_session_info(self, **_kwargs) -> None:
            return None

        def record_artifact_ref(self, _record) -> None:
            return None

        def record_orchestration_event(self, _event) -> None:
            return None

    monkeypatch.setattr(
        "murder.app.service.process_scope.resolve_recorder_mode",
        lambda: "full",
    )
    monkeypatch.setattr(
        "murder.app.service.process_scope.open_advanced_log",
        lambda *_a, **_k: _Adv(),
    )
    monkeypatch.setattr(
        "murder.app.service.process_scope.InProcessOrchestrationEventSink",
        _Events,
    )
    monkeypatch.setattr(
        "murder.app.service.process_scope.PersistingCommandSubmitter",
        lambda *_a, **_k: MagicMock(),
    )
    monkeypatch.setattr(
        "murder.app.service.process_scope.SqliteCommandRepository",
        lambda *_a, **_k: MagicMock(),
    )

    async def _drive() -> None:
        async with ProcessScope.open(_config(), repo_root):
            pass

    asyncio.run(_drive())
    assert order.index("recorder.cancel") < order.index("advanced_log.stop")


def test_wait_for_signal_wakes_on_installed_handler(repo_root: Path) -> None:
    async def _drive() -> None:
        async with ProcessScope.open(_config(), repo_root) as scope:
            task = asyncio.create_task(scope.wait_for_signal())
            await asyncio.sleep(0)
            os.kill(os.getpid(), signal.SIGTERM)
            await asyncio.wait_for(task, timeout=2)
            assert scope.is_external_stop_set()

    asyncio.run(_drive())


def test_double_close_is_harmless(repo_root: Path) -> None:
    async def _drive() -> None:
        async with ProcessScope.open(_config(), repo_root) as scope:
            await scope.close()
            await scope.close()

    asyncio.run(_drive())
    assert lock_is_held(lock_path(repo_root)) is False


def test_advanced_log_start_failure_clears_ambient_and_releases_flock(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Adv:
        async def start(self) -> None:
            raise RuntimeError("adv start boom")

        async def stop(self) -> None:
            return None

    monkeypatch.setattr(
        "murder.app.service.process_scope.open_advanced_log",
        lambda *_a, **_k: _Adv(),
    )
    monkeypatch.setattr(
        "murder.app.service.process_scope.resolve_recorder_mode",
        lambda: "off",
    )

    async def _drive() -> None:
        with pytest.raises(RuntimeError, match="adv start boom"):
            async with ProcessScope.open(_config(), repo_root):
                pass

    asyncio.run(_drive())
    assert lock_is_held(lock_path(repo_root)) is False
    # Ambient context must not leak the failed recorder.
    assert isinstance(current_advanced_log(), NullAdvancedLog)
