"""Phase 3: DispatcherLoops owns activity/trigger retry loops."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from murder.app.service.dispatcher_loops import DispatcherLoops


class _ActivityDispatcher:
    def __init__(self) -> None:
        self.calls = 0
        self._fail_once = True
        self.entered_retry = asyncio.Event()

    async def tick(self) -> None:
        self.calls += 1
        if self._fail_once:
            self._fail_once = False
            raise RuntimeError("transient activity failure")
        self.entered_retry.set()
        await asyncio.Event().wait()


class _TriggerDispatcher:
    def __init__(self) -> None:
        self.calls = 0
        self.entered = asyncio.Event()

    def tick(self) -> None:
        self.calls += 1
        if self.calls == 1:
            self.entered.set()
            raise RuntimeError("transient trigger failure")
        self.entered.set()


def test_factory_absence_produces_no_tasks(repo_root: Path) -> None:
    del repo_root

    async def _drive() -> DispatcherLoops:
        async with DispatcherLoops.open(
            db=MagicMock(),
            session_controllers=MagicMock(),
            activity_factory=None,
            trigger_factory=None,
        ) as loops:
            assert loops.activity_dispatcher is None
            assert loops.trigger_dispatcher is None
            assert loops._tasks == {}  # noqa: SLF001
            return loops

    loops = asyncio.run(_drive())
    assert loops._tasks == {}  # noqa: SLF001


def test_tick_exceptions_retry_and_cancellation_is_not_logged_as_failure(
    repo_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    del repo_root
    activity = _ActivityDispatcher()
    trigger = _TriggerDispatcher()

    async def _drive() -> tuple[int, int, set[str]]:
        async with DispatcherLoops.open(
            db=MagicMock(),
            session_controllers=MagicMock(),
            activity_factory=lambda _db, _reg: activity,
            trigger_factory=lambda _db: trigger,
            retry_delay_seconds=0,
        ) as loops:
            await asyncio.wait_for(activity.entered_retry.wait(), timeout=1)
            await asyncio.wait_for(trigger.entered.wait(), timeout=1)
            task_names = set(loops._tasks)  # noqa: SLF001
        return activity.calls, trigger.calls, task_names

    with caplog.at_level(logging.ERROR, logger="murder.app.service.dispatcher_loops"):
        activity_calls, trigger_calls, task_names = asyncio.run(_drive())

    assert activity_calls >= 2
    assert trigger_calls >= 1
    assert task_names == {"phase4-activities", "phase4-triggers"}
    assert any("activity dispatcher tick failed" in r.message for r in caplog.records)
    assert any("trigger dispatcher tick failed" in r.message for r in caplog.records)
    assert not any("CancelledError" in r.message for r in caplog.records)


def test_close_drains_both_loops(repo_root: Path) -> None:
    del repo_root
    hang = asyncio.Event()

    class _HangActivity:
        async def tick(self) -> None:
            await hang.wait()

    class _HangTrigger:
        def tick(self) -> None:
            return None

    async def _drive() -> list[asyncio.Task[None]]:
        loops = DispatcherLoops(retry_delay_seconds=0)
        await loops.start(
            db=MagicMock(),
            session_controllers=MagicMock(),
            activity_factory=lambda _db, _reg: _HangActivity(),
            trigger_factory=lambda _db: _HangTrigger(),
        )
        tasks = list(loops._tasks.values())  # noqa: SLF001
        await loops.close()
        return tasks

    tasks = asyncio.run(_drive())
    assert all(task.cancelled() or task.done() for task in tasks)


def test_trigger_sync_tick_is_adapted(repo_root: Path) -> None:
    """TriggerDispatcher.tick() is sync; DispatcherLoops wraps it awaitably."""
    del repo_root
    seen: list[str] = []

    class _Trigger:
        def tick(self) -> None:
            seen.append("tick")
            raise RuntimeError("stop after first")

    async def _drive() -> None:
        loops = DispatcherLoops(retry_delay_seconds=0)
        await loops.start(
            db=MagicMock(),
            session_controllers=MagicMock(),
            activity_factory=None,
            trigger_factory=lambda _db: _Trigger(),
        )
        await asyncio.sleep(0)
        await loops.close()

    asyncio.run(_drive())
    assert seen == ["tick"]
