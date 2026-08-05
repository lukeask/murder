"""Background filesystem↔SQLite sync loops (W3 Runtime narrow)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from murder.state.persistence.connection import RepoDb
from murder.work.attribution import attribute_edit
from murder.work.examples import seed_examples
from murder.work.notes.sync import NoteSync, NotetakerContextSync
from murder.work.plans.sync import PlanSync
from murder.work.reports.sync import ReportSync
from murder.work.tickets.sync import TicketSync

LOGGER = logging.getLogger(__name__)

# Deliver a free-form message to an agent by id. Wired to the orchestrator's
# `send_agent_message` once it exists; the host injects the notifier before
# `start()` / `running()` so loops never begin without delivery configured.
MessageSender = Callable[[str, str], Awaitable[None]]


def _build_parse_error_message(path: Path, parse_error: str) -> str:
    """The fix-prompt sent to the owning agent for a malformed artifact."""
    return (
        f"The file you edited at `{path}` failed to parse and did not save:\n"
        f"  {parse_error}\n"
        "Re-open that file, fix the malformed frontmatter or content, and "
        "save it again so the system can load it."
    )


SYNC_TASK_KEYS = (
    "plan_sync",
    "note_sync",
    "notetaker_context_sync",
    "ticket_sync",
    "report_sync",
)


@dataclass
class FilesystemSyncService:
    """Owns plan/note/ticket/report sync instances and their background tasks."""

    plan_sync: PlanSync
    note_sync: NoteSync
    notetaker_context_sync: NotetakerContextSync
    ticket_sync: TicketSync
    report_sync: ReportSync
    repo_root: Path

    _tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict, init=False, repr=False)
    _running: bool = field(default=False, init=False, repr=False)
    _parse_error_notifier: MessageSender | None = field(default=None, init=False, repr=False)

    @classmethod
    def attach(
        cls,
        repo_root: Path,
        db: RepoDb,
    ) -> FilesystemSyncService:
        service = cls(
            plan_sync=PlanSync(repo_root, db),
            note_sync=NoteSync(repo_root, db),
            notetaker_context_sync=NotetakerContextSync(repo_root, db),
            ticket_sync=TicketSync(repo_root, db),
            report_sync=ReportSync(repo_root, db),
            repo_root=repo_root,
        )
        return service

    def set_parse_error_notifier(self, send_message: MessageSender) -> None:
        """Route malformed-artifact parse errors to the owning agent.

        Must be called before ``start()`` / ``running()``. Late mutation after
        loops are already running is rejected.
        """
        if self._running:
            raise RuntimeError("cannot install parse-error notifier while sync loops are running")
        self._parse_error_notifier = send_message
        self._install_parse_error_notifier(send_message)

    def _install_parse_error_notifier(self, send_message: MessageSender) -> None:
        repo_root = self.repo_root

        async def _notify(path: Path, parse_error: str) -> None:
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
        """Restore any missing example artifacts. Cheap and idempotent. Kept on the boot path."""
        seed_examples(self.repo_root)

    async def reconcile_all(self) -> None:
        self.seed()
        await self.plan_sync.reconcile_all()
        await self.note_sync.reconcile_all()
        await self.notetaker_context_sync.reconcile_all()
        await self.ticket_sync.reconcile_all()
        await self.report_sync.reconcile_all()

    def _require_parse_error_notifier(self) -> MessageSender:
        if self._parse_error_notifier is None:
            raise RuntimeError(
                "parse-error notifier must be installed via set_parse_error_notifier() "
                "before start()/running(); seed()/reconcile_all() do not require it"
            )
        return self._parse_error_notifier

    async def start(self) -> None:
        """Spawn owned sync loops. Idempotent if already running.

        Requires ``set_parse_error_notifier`` first so parse errors cannot be
        silently dropped. ``seed`` / ``reconcile_all`` remain notifier-free.
        """
        if self._running:
            return
        notifier = self._require_parse_error_notifier()
        self._install_parse_error_notifier(notifier)
        self._tasks = {
            "plan_sync": asyncio.create_task(self.plan_sync.run(), name="plan_sync"),
            "note_sync": asyncio.create_task(self.note_sync.run(), name="note_sync"),
            "notetaker_context_sync": asyncio.create_task(
                self.notetaker_context_sync.run(), name="notetaker_context_sync"
            ),
            "ticket_sync": asyncio.create_task(self.ticket_sync.run(), name="ticket_sync"),
            "report_sync": asyncio.create_task(self.report_sync.run(), name="report_sync"),
        }
        self._running = True

    async def close(self) -> None:
        """Cancel owned sync tasks, drain, then reconcile once more if loops ran.

        Never-started services skip the final reconcile so startup rollback does
        not pay for a full filesystem scan after a later boot step fails.
        """
        was_started = self._running or bool(self._tasks)
        for key in SYNC_TASK_KEYS:
            task = self._tasks.pop(key, None)
            if task is None:
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        self._running = False
        if was_started:
            with contextlib.suppress(Exception):
                await self.reconcile_all()

    @asynccontextmanager
    async def running(self) -> AsyncIterator[FilesystemSyncService]:
        await self.start()
        try:
            yield self
        finally:
            await self.close()


__all__ = ["FilesystemSyncService", "SYNC_TASK_KEYS"]
