"""Background filesystem↔SQLite sync loops (W3 Runtime narrow)."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from pathlib import Path

import sqlite3

from murder.work.notes.sync import NoteSync
from murder.work.notes.sync import NotetakerContextSync
from murder.work.plans.sync import PlanSync
from murder.work.tickets.sidecar_sync import TicketMetadataSync
from murder.work.tickets.sync import TicketSync

SYNC_TASK_KEYS = (
    "plan_sync",
    "note_sync",
    "notetaker_context_sync",
    "ticket_sync",
    "ticket_metadata_sync",
)


@dataclass
class FilesystemSyncSupervisor:
    """Owns plan/note/ticket sync instances and their background tasks."""

    plan_sync: PlanSync
    note_sync: NoteSync
    notetaker_context_sync: NotetakerContextSync
    ticket_sync: TicketSync
    ticket_metadata_sync: TicketMetadataSync

    @classmethod
    def attach(cls, repo_root: Path, db: sqlite3.Connection) -> FilesystemSyncSupervisor:
        return cls(
            plan_sync=PlanSync(repo_root, db),
            note_sync=NoteSync(repo_root, db),
            notetaker_context_sync=NotetakerContextSync(repo_root, db),
            ticket_sync=TicketSync(repo_root, db),
            ticket_metadata_sync=TicketMetadataSync(repo_root, db),
        )

    async def reconcile_all(self) -> None:
        await self.plan_sync.reconcile_all()
        await self.note_sync.reconcile_all()
        await self.notetaker_context_sync.reconcile_all()
        await self.ticket_sync.reconcile_all()
        await self.ticket_metadata_sync.reconcile_all()

    def spawn_tasks(self) -> dict[str, asyncio.Task[None]]:
        return {
            "plan_sync": asyncio.create_task(self.plan_sync.run()),
            "note_sync": asyncio.create_task(self.note_sync.run()),
            "notetaker_context_sync": asyncio.create_task(self.notetaker_context_sync.run()),
            "ticket_sync": asyncio.create_task(self.ticket_sync.run()),
            "ticket_metadata_sync": asyncio.create_task(self.ticket_metadata_sync.run()),
        }

    async def shutdown(self, tasks: dict[str, asyncio.Task[None]]) -> None:
        """Cancel sync background tasks, drain, then reconcile once more."""
        for key in SYNC_TASK_KEYS:
            task = tasks.pop(key, None)
            if task is None:
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        with contextlib.suppress(Exception):
            await self.reconcile_all()


__all__ = ["FilesystemSyncSupervisor", "SYNC_TASK_KEYS"]
