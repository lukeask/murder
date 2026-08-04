"""Phase 3: FilesystemSyncService owns its tasks and notifier-before-start."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from murder.app.service.filesystem_sync import SYNC_TASK_KEYS, FilesystemSyncService
from tests.support.database import open_test_repo_db


def test_start_creates_owned_tasks(repo_root: Path, tmp_path: Path) -> None:
    db = open_test_repo_db(tmp_path / "murder.db")
    service = FilesystemSyncService.attach(repo_root, db)

    async def _drive() -> dict[str, asyncio.Task[None]]:
        await service.start()
        tasks = dict(service._tasks)  # noqa: SLF001
        await service.close()
        return tasks

    try:
        tasks = asyncio.run(_drive())
    finally:
        db.close()

    assert set(tasks) == set(SYNC_TASK_KEYS)
    assert all(task.cancelled() or task.done() for task in tasks.values())


def test_parse_notifier_must_be_installed_before_start(repo_root: Path, tmp_path: Path) -> None:
    db = open_test_repo_db(tmp_path / "murder.db")
    service = FilesystemSyncService.attach(repo_root, db)
    sent: list[tuple[str, str]] = []

    async def fake_send(agent_id: str, message: str) -> None:
        sent.append((agent_id, message))

    async def _drive() -> None:
        service.set_parse_error_notifier(fake_send)
        assert service.plan_sync.parse_error_notifier is not None
        assert service.ticket_sync.parse_error_notifier is not None
        await service.start()
        with pytest.raises(RuntimeError, match="while sync loops are running"):
            service.set_parse_error_notifier(fake_send)
        await service.close()

    try:
        asyncio.run(_drive())
    finally:
        db.close()


def test_close_without_start_skips_final_reconcile(repo_root: Path) -> None:
    order: list[str] = []

    class _Doc:
        async def run(self) -> None:
            await asyncio.Event().wait()

        async def reconcile_all(self) -> None:
            order.append("reconcile")

    service = FilesystemSyncService(
        plan_sync=_Doc(),  # type: ignore[arg-type]
        note_sync=_Doc(),  # type: ignore[arg-type]
        notetaker_context_sync=_Doc(),  # type: ignore[arg-type]
        ticket_sync=_Doc(),  # type: ignore[arg-type]
        report_sync=_Doc(),  # type: ignore[arg-type]
        repo_root=repo_root,
    )

    async def _tracked_reconcile() -> None:
        order.append("reconcile_all")

    service.reconcile_all = _tracked_reconcile  # type: ignore[method-assign]
    asyncio.run(service.close())
    assert order == []


def test_close_cancels_tasks_and_reconciles(repo_root: Path) -> None:
    order: list[str] = []

    class _Doc:
        async def run(self) -> None:
            await asyncio.Event().wait()

        async def reconcile_all(self) -> None:
            order.append("reconcile")

    service = FilesystemSyncService(
        plan_sync=_Doc(),  # type: ignore[arg-type]
        note_sync=_Doc(),  # type: ignore[arg-type]
        notetaker_context_sync=_Doc(),  # type: ignore[arg-type]
        ticket_sync=_Doc(),  # type: ignore[arg-type]
        report_sync=_Doc(),  # type: ignore[arg-type]
        repo_root=repo_root,
    )

    async def _tracked_reconcile() -> None:
        order.append("reconcile_all")

    service.reconcile_all = _tracked_reconcile  # type: ignore[method-assign]

    async def _drive() -> list[asyncio.Task[None]]:
        await service.start()
        owned = list(service._tasks.values())  # noqa: SLF001
        await service.close()
        return owned

    owned = asyncio.run(_drive())
    assert all(task.cancelled() for task in owned)
    assert service._tasks == {}  # noqa: SLF001
    assert order == ["reconcile_all"]


def test_running_context_manager_starts_and_closes(repo_root: Path) -> None:
    class _Doc:
        async def run(self) -> None:
            await asyncio.Event().wait()

        async def reconcile_all(self) -> None:
            return None

    service = FilesystemSyncService(
        plan_sync=_Doc(),  # type: ignore[arg-type]
        note_sync=_Doc(),  # type: ignore[arg-type]
        notetaker_context_sync=_Doc(),  # type: ignore[arg-type]
        ticket_sync=_Doc(),  # type: ignore[arg-type]
        report_sync=_Doc(),  # type: ignore[arg-type]
        repo_root=repo_root,
    )

    async def _drive() -> bool:
        async with service.running() as running:
            assert running is service
            assert service._running is True  # noqa: SLF001
            assert set(service._tasks) == set(SYNC_TASK_KEYS)  # noqa: SLF001
        return service._running  # noqa: SLF001

    assert asyncio.run(_drive()) is False
