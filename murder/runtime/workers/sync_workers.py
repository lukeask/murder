from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from pathlib import Path

from murder.work.notes.sync import NoteSync
from murder.work.plans.sync import PlanSync
from murder.runtime.workers.base import Worker, WorkerCtx, WorkerSpec
from murder.runtime.orchestration.worker_names import WorkerName


class PlanSyncWorker(Worker):
    def __init__(
        self,
        repo_root: Path,
        db: sqlite3.Connection,
        *,
        poll_s: float = 1.5,
        debounce_s: float = 0.75,
    ) -> None:
        super().__init__(WorkerSpec(name=WorkerName.PLAN_SYNC, heartbeat_s=poll_s))
        self._sync = PlanSync(repo_root, db, poll_s=poll_s, debounce_s=debounce_s)

    async def run(self, ctx: WorkerCtx, stop_event: asyncio.Event) -> None:  # noqa: ARG002
        task = asyncio.create_task(self._sync.run())
        try:
            stop_wait = asyncio.create_task(stop_event.wait())
            done, pending = await asyncio.wait(
                {task, stop_wait},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for pending_task in pending:
                pending_task.cancel()
            if task in done:
                await task
        finally:
            self._sync._running = False  # bounded refactor: preserve existing loop behavior
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


class NoteSyncWorker(Worker):
    def __init__(
        self,
        repo_root: Path,
        db: sqlite3.Connection,
        *,
        poll_s: float = 1.5,
        debounce_s: float = 0.75,
    ) -> None:
        super().__init__(WorkerSpec(name=WorkerName.NOTE_SYNC, heartbeat_s=poll_s))
        self._sync = NoteSync(repo_root, db, poll_s=poll_s, debounce_s=debounce_s)

    async def run(self, ctx: WorkerCtx, stop_event: asyncio.Event) -> None:  # noqa: ARG002
        task = asyncio.create_task(self._sync.run())
        try:
            stop_wait = asyncio.create_task(stop_event.wait())
            done, pending = await asyncio.wait(
                {task, stop_wait},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for pending_task in pending:
                pending_task.cancel()
            if task in done:
                await task
        finally:
            self._sync._running = False  # bounded refactor: preserve existing loop behavior
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
