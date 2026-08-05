"""Graceful vs authoritative shutdown tmux policy (§7.2 / §11).

Authoritative ``RepositoryHost.stop()`` (murder down) clears the external-stop
signal and runs the project-wide tmux sweep. A graceful signal stop leaves the
external-stop flag set so the sweep is skipped and Crow panes survive for
reattach.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from murder.app.service.filesystem_sync import FilesystemSyncService
from murder.app.service.repository_host import RepositoryHost
from murder.config import (
    Config,
    CrowHandlerConfig,
    HarnessRoleConfig,
    ProjectConfig,
)


def _config() -> Config:
    return Config(
        project=ProjectConfig(name="repo"),
        collaborator=HarnessRoleConfig(harness="codex"),
        default_crow=HarnessRoleConfig(harness="codex"),
        crow_handler=CrowHandlerConfig(model="test-model"),
    )


class _NoopDispatcher:
    async def tick(self) -> None:
        return None


def _patch_host_peripherals(monkeypatch: pytest.MonkeyPatch, swept: list[object]) -> None:
    async def _fake_kill(session_names: object) -> list[str]:
        swept.append(session_names)
        return []

    monkeypatch.setattr(
        "murder.app.service.repository_host.kill_project_tmux_sessions",
        _fake_kill,
    )

    @asynccontextmanager
    async def _noop_running(self):  # noqa: ANN001
        yield self

    monkeypatch.setattr(FilesystemSyncService, "running", _noop_running)
    monkeypatch.setattr(FilesystemSyncService, "seed", lambda self: None)

    async def _fake_supervisor(**_kwargs: object) -> MagicMock:
        supervisor = MagicMock()
        supervisor.stop_all = AsyncMock()
        return supervisor

    monkeypatch.setattr(
        "murder.app.service.repository_host.start_supervisor_workers",
        _fake_supervisor,
    )
    monkeypatch.setattr(
        "murder.app.service.background_tasks.ServiceBackgroundTasks.start",
        lambda self: None,
    )
    monkeypatch.setattr(
        "murder.app.service.background_tasks.ServiceBackgroundTasks.stop",
        AsyncMock(),
    )


async def _start_host(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch, swept: list
) -> RepositoryHost:
    _patch_host_peripherals(monkeypatch, swept)
    host = RepositoryHost(
        config=_config(),
        repo_root=repo_root,
        activity_dispatcher_factory=lambda _db, _reg: _NoopDispatcher(),  # type: ignore[return-value,arg-type]
        trigger_dispatcher_factory=lambda _db: _NoopDispatcher(),  # type: ignore[return-value,arg-type]
    )
    await host.start()
    return host


@pytest.mark.integration
def test_authoritative_stop_sweeps_project_tmux(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    swept: list[object] = []

    async def _drive() -> None:
        host = await _start_host(repo_root, monkeypatch, swept)
        assert host._lifecycle_committed is True
        # Simulate a prior SIGTERM so the flag is set, then murder-down clears it.
        host._running.process._external_stop.set()  # noqa: SLF001
        assert host._running.process.is_external_stop_set()
        await host.stop()

    asyncio.run(_drive())
    assert len(swept) == 1


@pytest.mark.integration
def test_graceful_stop_preserves_project_tmux(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    swept: list[object] = []

    async def _drive() -> None:
        host = await _start_host(repo_root, monkeypatch, swept)
        # Graceful path: external stop remains set and stop() must not clear it.
        # Drive stack unwind directly so we do not hit RepositoryHost.stop()'s
        # authoritative clear_external_stop().
        host._running.process._external_stop.set()  # noqa: SLF001
        assert host._running.process.is_external_stop_set()
        stack = host._stack
        assert stack is not None
        # Tear down peripherals the same way stop() would, without clearing signal.
        if host.background_tasks is not None:
            await host.background_tasks.stop()
            host.background_tasks = None
        if host.supervisor is not None:
            await host.supervisor.stop_all()
            host.supervisor = None
        await stack.aclose()
        host._stack = None
        host._running = None
        host._lifecycle_committed = False

    asyncio.run(_drive())
    assert swept == []
