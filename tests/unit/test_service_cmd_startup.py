from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from murder.app.cli import service_cmd
from murder.state.storage.filesystem import acquire_flock, lock_is_held, release_flock


def test_lock_is_held_ignores_stale_lockfile(tmp_path: Path) -> None:
    path = tmp_path / ".lock"
    path.write_text("123\n", encoding="ascii")

    assert lock_is_held(path) is False


def test_lock_is_held_detects_kernel_flock(tmp_path: Path) -> None:
    path = tmp_path / ".lock"
    fd = acquire_flock(path)
    try:
        assert lock_is_held(path) is True
    finally:
        release_flock(fd)


def test_live_lock_owner_ignores_reused_pid_in_stale_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / ".murder" / ".lock"
    path.parent.mkdir()
    path.write_text("123\n", encoding="ascii")
    monkeypatch.setattr(service_cmd, "_pid_is_alive", lambda _pid: True)

    assert service_cmd._live_lock_owner_pid(tmp_path) is None


async def test_ensure_supervisor_waits_for_live_lock_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    live = AsyncMock(side_effect=[False, True])
    spawn = Mock()
    monkeypatch.setattr(service_cmd, "_supervisor_is_live", live)
    monkeypatch.setattr(service_cmd, "_live_lock_owner_pid", lambda _repo: 123)
    monkeypatch.setattr(service_cmd, "_spawn_service_process", spawn)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    started = await service_cmd._ensure_supervisor_impl(tmp_path, tmp_path / "bus.sock")

    assert started is False
    spawn.assert_not_called()


async def test_ensure_supervisor_follows_concurrent_winner_when_our_child_exits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    live = AsyncMock(side_effect=[False, False, True])
    owners = iter([None, 456, 456])
    proc = Mock(pid=123)
    proc.poll.return_value = 1
    spawn = Mock(return_value=proc)
    monkeypatch.setattr(service_cmd, "_supervisor_is_live", live)
    monkeypatch.setattr(service_cmd, "_live_lock_owner_pid", lambda _repo: next(owners))
    monkeypatch.setattr(service_cmd, "_spawn_service_process", spawn)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    started = await service_cmd._ensure_supervisor_impl(tmp_path, tmp_path / "bus.sock")

    assert started is False
    spawn.assert_called_once_with(tmp_path)


async def test_ensure_supervisor_reports_our_child_startup_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    proc = Mock(pid=123)
    proc.poll.return_value = 1
    monkeypatch.setattr(
        service_cmd,
        "_supervisor_is_live",
        AsyncMock(side_effect=[False, False]),
    )
    monkeypatch.setattr(service_cmd, "_live_lock_owner_pid", lambda _repo: None)
    monkeypatch.setattr(service_cmd, "_spawn_service_process", Mock(return_value=proc))
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    with pytest.raises(
        RuntimeError,
        match=r"supervisor process exited during startup \(code 1\)",
    ):
        await service_cmd._ensure_supervisor_impl(tmp_path, tmp_path / "bus.sock")
