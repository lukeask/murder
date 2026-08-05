from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from typer.testing import CliRunner

from murder.app.cli import app, service_cmd
from murder.app.service import daemon_host as daemon_host_mod
from murder.state.storage.filesystem import acquire_flock, lock_is_held, release_flock

SERVICE_PID = 123


def test_serviced_has_no_websocket_port_option() -> None:
    """Port overrides must not be user-facing; clients hard-code 62077."""
    params = inspect.signature(service_cmd.cmd_serviced).parameters
    assert "websocket_port" not in params

    result = CliRunner().invoke(app, ["serviced", "--websocket-port", "9999"])
    assert result.exit_code != 0
    combined = result.output.lower()
    assert "no such option" in combined or "unexpected" in combined


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


def test_release_flock_leaves_lockfile_inode_intact(tmp_path: Path) -> None:
    """flock exclusivity is inode-bound; the path must never be unlinked/recreated."""
    path = tmp_path / "daemon.lock"
    fd = acquire_flock(path)
    inode = path.stat().st_ino
    release_flock(fd)

    assert path.is_file()
    assert path.stat().st_ino == inode
    assert lock_is_held(path) is False

    fd2 = acquire_flock(path)
    try:
        assert path.stat().st_ino == inode
    finally:
        release_flock(fd2)


def test_live_daemon_ignores_reused_pid_in_stale_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "daemon.lock"
    path.write_text("123\n", encoding="ascii")
    monkeypatch.setattr(daemon_host_mod, "daemon_lock_path", lambda: path)
    monkeypatch.setattr(daemon_host_mod.os, "kill", lambda *_a, **_k: None)

    assert service_cmd._live_lock_owner_pid() is None


def test_signal_daemon_does_not_unlink_when_lock_already_free(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "daemon.lock"
    path.write_text("999\n", encoding="ascii")
    remove = Mock()
    monkeypatch.setattr(service_cmd, "live_daemon_pid", lambda: None)
    monkeypatch.setattr(service_cmd, "remove_daemon_record", remove)

    service_cmd._signal_daemon(999)

    assert path.is_file()
    remove.assert_called_once_with()


def test_signal_daemon_does_not_unlink_on_process_lookup_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "daemon.lock"
    path.write_text("123\n", encoding="ascii")
    remove = Mock()
    monkeypatch.setattr(service_cmd, "live_daemon_pid", lambda: 123)
    monkeypatch.setattr(service_cmd, "remove_daemon_record", remove)

    def _kill(_pid: int, _sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(service_cmd.os, "kill", _kill)

    service_cmd._signal_daemon(123)

    assert path.is_file()
    remove.assert_called_once_with()


def test_signal_daemon_does_not_unlink_after_clean_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "daemon.lock"
    path.write_text("123\n", encoding="ascii")
    remove = Mock()
    monkeypatch.setattr(service_cmd, "live_daemon_pid", lambda: 123)
    monkeypatch.setattr(service_cmd, "remove_daemon_record", remove)
    monkeypatch.setattr(service_cmd.os, "kill", Mock())
    monkeypatch.setattr(service_cmd, "_pid_is_alive", lambda _pid: False)

    service_cmd._signal_daemon(123)

    assert path.is_file()
    remove.assert_called_once_with()


async def test_daemon_ready_when_listener_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service_cmd, "probe_daemon_listener", AsyncMock(return_value=True))
    assert await service_cmd._daemon_is_ready() is True


async def test_daemon_not_ready_when_listener_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service_cmd, "probe_daemon_listener", AsyncMock(return_value=False))
    assert await service_cmd._daemon_is_ready() is False


async def test_ensure_daemon_waits_for_live_lock_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ready = AsyncMock(side_effect=[False, True])
    spawn = Mock()
    monkeypatch.setattr(service_cmd, "_daemon_is_ready", ready)
    monkeypatch.setattr(service_cmd, "_live_lock_owner_pid", lambda: 123)
    monkeypatch.setattr(service_cmd, "_spawn_daemon_process", spawn)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    started = await service_cmd._ensure_daemon_impl(spawn_cwd=tmp_path)

    assert started is False
    spawn.assert_not_called()


async def test_ensure_daemon_follows_concurrent_winner_when_our_child_exits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ready = AsyncMock(side_effect=[False, False, True])
    owners = iter([None, 456, 456])
    proc = Mock(pid=123)
    proc.poll.return_value = 1
    spawn = Mock(return_value=proc)
    monkeypatch.setattr(service_cmd, "_daemon_is_ready", ready)
    monkeypatch.setattr(service_cmd, "_live_lock_owner_pid", lambda: next(owners))
    monkeypatch.setattr(service_cmd, "_spawn_daemon_process", spawn)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    started = await service_cmd._ensure_daemon_impl(spawn_cwd=tmp_path)

    assert started is False
    spawn.assert_called_once()


async def test_ensure_daemon_reports_our_child_startup_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    proc = Mock(pid=123)
    proc.poll.return_value = 1
    monkeypatch.setattr(
        service_cmd,
        "_daemon_is_ready",
        AsyncMock(side_effect=[False, False]),
    )
    monkeypatch.setattr(service_cmd, "_live_lock_owner_pid", lambda: None)
    monkeypatch.setattr(service_cmd, "_spawn_daemon_process", Mock(return_value=proc))
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    with pytest.raises(
        RuntimeError,
        match=r"daemon process exited during startup \(code 1\)",
    ):
        await service_cmd._ensure_daemon_impl(spawn_cwd=tmp_path)


async def test_ensure_daemon_attaches_when_listener_already_live(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spawn = Mock()
    monkeypatch.setattr(service_cmd, "_daemon_is_ready", AsyncMock(return_value=True))
    monkeypatch.setattr(service_cmd, "_spawn_daemon_process", spawn)

    started = await service_cmd._ensure_daemon_impl(spawn_cwd=tmp_path)

    assert started is False
    spawn.assert_not_called()


async def test_ensure_daemon_and_activate_posts_after_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    activate = AsyncMock(return_value={"repository_id": "r1", "websocket_url": "ws://x"})
    monkeypatch.setattr(service_cmd, "_ensure_daemon_started", AsyncMock(return_value=True))
    monkeypatch.setattr(service_cmd, "_activate_repository", activate)

    started, info = await service_cmd.ensure_daemon_and_activate(tmp_path)

    assert started is True
    assert info["repository_id"] == "r1"
    activate.assert_awaited_once_with(tmp_path)
