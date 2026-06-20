"""Background filesystem↔SQLite sync loops (W3 Runtime narrow)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from murder.work.attribution import attribute_edit
from murder.work.examples import seed_examples
from murder.work.notes.sync import NoteSync, NotetakerContextSync
from murder.work.plans.sync import PlanSync
from murder.work.reports.sync import ReportSync
from murder.work.tickets.sync import TicketSync

if TYPE_CHECKING:
    from murder.bus import Bus
    from murder.bus.protocol import Entity, StateSnapshotEvent

LOGGER = logging.getLogger(__name__)

# Deliver a free-form message to an agent by id. Wired to the orchestrator's
# `send_agent_message` once it exists (the orchestrator is built after the sync
# loops start, so the notifier is attached late via `set_parse_error_notifier`).
MessageSender = Callable[[str, str], Awaitable[None]]


def _build_parse_error_message(path: Path, parse_error: str) -> str:
    """The fix-prompt sent to the owning agent for a malformed artifact."""
    return (
        f"The file you edited at `{path}` failed to parse and was not saved:\n"
        f"  {parse_error}\n"
        "Please re-open that file, fix the malformed frontmatter/content, and "
        "save it again so it can be ingested."
    )

SYNC_TASK_KEYS = (
    "plan_sync",
    "note_sync",
    "notetaker_context_sync",
    "ticket_sync",
    "report_sync",
)


@dataclass
class FilesystemSyncSupervisor:
    """Owns plan/note/ticket/report sync instances and their background tasks."""

    plan_sync: PlanSync
    note_sync: NoteSync
    notetaker_context_sync: NotetakerContextSync
    ticket_sync: TicketSync
    report_sync: ReportSync

    repo_root: Path | None = None
    # Bus handle + run_id for the async emit seam (F5.1).  Set at construct time
    # when the broker exists (Runtime.start).  Kept here so the _emit callback
    # is the one ``on_change`` passed to each SimpleDocSync (notes, reports).
    _bus: "Bus | None" = field(default=None, repr=False)
    _run_id: str | None = field(default=None, repr=False)

    async def _emit(self, entity: "Entity", key: str) -> None:
        """Async emit helper for the F5.1 notify_changed seam.

        Publishes a key-only StateSnapshotEvent from the filesystem sync path.
        No-ops if bus or run_id are not available (mirrors Runtime.emit_snapshot
        safety guard).  This is the callback passed as ``on_change`` to each
        MarkdownSyncLoop that uses the F5.1 notify_changed seam.
        """
        if self._bus is None or self._run_id is None:
            return
        from murder.bus.protocol import StateSnapshotEvent  # avoid top-level circular

        await self._bus.publish(
            StateSnapshotEvent(
                run_id=self._run_id,
                agent_id="filesystem-sync",
                entity=entity,
                key=key,
            )
        )

    @classmethod
    def attach(
        cls,
        repo_root: Path,
        db: sqlite3.Connection,
        *,
        on_ticket_change: Callable[[str], None] | None = None,
        on_plan_change: Callable[[str], None] | None = None,
        bus: "Bus | None" = None,
        run_id: str | None = None,
    ) -> "FilesystemSyncSupervisor":
        # Build the supervisor first so _emit is available as the on_change
        # callback for notes and reports.  We need the instance before we can
        # reference _emit, so we build it in two steps.
        sup = cls.__new__(cls)
        # Initialise dataclass fields manually (avoid __init__ arg ordering issues).
        sup.repo_root = repo_root
        sup._bus = bus
        sup._run_id = run_id

        sup.plan_sync = PlanSync(repo_root, db, on_plan_change=on_plan_change)
        sup.note_sync = NoteSync(repo_root, db, on_change=sup._emit)
        sup.notetaker_context_sync = NotetakerContextSync(repo_root, db)
        sup.ticket_sync = TicketSync(repo_root, db, on_ticket_change=on_ticket_change)
        sup.report_sync = ReportSync(repo_root, db, on_change=sup._emit)
        return sup

    def set_parse_error_notifier(self, send_message: MessageSender) -> None:
        """Route malformed-artifact parse errors to the owning agent.

        Attached late (after the orchestrator exists) because the sync loops
        are constructed during `Runtime.start`, before the orchestrator that
        delivers `agent.message`. ``attribute_edit`` maps the path → owner id;
        an unattributable path is logged and dropped.
        """
        repo_root = self.repo_root

        async def _notify(path: Path, parse_error: str) -> None:
            if repo_root is None:
                return
            agent_id = attribute_edit(path, repo_root=repo_root)
            if agent_id is None:
                LOGGER.debug("parse_error for unattributable artifact %s; not notifying", path)
                return
            message = _build_parse_error_message(path, parse_error)
            try:
                await send_message(agent_id, message)
            except Exception:
                LOGGER.exception("failed to notify %s of parse error in %s", agent_id, path)

        self.plan_sync.parse_error_notifier = _notify
        self.ticket_sync.parse_error_notifier = _notify

    def seed(self) -> None:
        """Restore any missing example artifacts. Cheap + idempotent; kept on the boot path."""
        if self.repo_root is not None:
            seed_examples(self.repo_root)

    async def reconcile_all(self) -> None:
        self.seed()
        await self.plan_sync.reconcile_all()
        await self.note_sync.reconcile_all()
        await self.notetaker_context_sync.reconcile_all()
        await self.ticket_sync.reconcile_all()
        await self.report_sync.reconcile_all()

    def spawn_tasks(self) -> dict[str, asyncio.Task[None]]:
        return {
            "plan_sync": asyncio.create_task(self.plan_sync.run()),
            "note_sync": asyncio.create_task(self.note_sync.run()),
            "notetaker_context_sync": asyncio.create_task(self.notetaker_context_sync.run()),
            "ticket_sync": asyncio.create_task(self.ticket_sync.run()),
            "report_sync": asyncio.create_task(self.report_sync.run()),
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
