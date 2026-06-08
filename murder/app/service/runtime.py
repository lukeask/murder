"""Long-lived async runtime + supervisor.

Owns the asyncio loop, the SQLite connection, the bus, and the lifecycle
of all agents. The TUI is one consumer in this same loop (D1: single
process). Daemons (CrowHandler, Sentinel) are coroutines spawned and supervised
here; their "tmux session" is a logfile being tailed for debug
visibility, not a real interactive session.

Process model rules:
- One murder process per repo. flock on `.murder/.lock` enforces.
- Graceful shutdown drains the bus, signals Crows, kills tmux sessions.
- Crash recovery: on startup, reconcile DB ↔ tmux ↔ filesystem before
  resuming.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import signal
import sqlite3
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from murder.app.service.agent_registry import AgentRegistry
from murder.app.service.document_access import DocumentAccess
from murder.app.service.filesystem_sync import FilesystemSyncSupervisor
from murder.app.service.recovery import reconcile_agents_vs_tmux
from murder.app.service.runtime_lifecycle import shutdown_live_agents
from murder.bus import Bus, EventFilter, SubscriptionHandle
from murder.llm.harnesses.versioning import HarnessVersionRegistry
from murder.runtime.agents.events import AgentEventSink, LoggingAgentEventSink
from murder.runtime.terminal import tmux
from murder.state.persistence.agents import (
    set_agent_status as _db_set_agent_status,
)
from murder.state.persistence.agents import (
    upsert_agent as _db_upsert_agent,
)
from murder.state.persistence.conversation import mark_stale_conversations
from murder.state.persistence.runs import end_run as _db_end_run
from murder.state.persistence.runs import insert_run as _db_insert_run
from murder.state.persistence.schema import get_db as _db_connect
from murder.state.persistence.schema import init_db as _db_init_schema
from murder.state.storage.filesystem import acquire_flock, release_flock
from murder.state.storage.paths import db_path, lock_path
from murder.state.storage.run_id_allocation import allocate_run_id

if TYPE_CHECKING:
    from murder.config import Config
    from murder.runtime.agents.base import LifecycleParticipant
    from murder.work.notes.sync import NoteSync, NotetakerContextSync
    from murder.work.plans.sync import PlanSync
    from murder.work.tickets.sync import TicketSync

Handler = Callable[[Any], Awaitable[None]]


class Runtime:
    """Async context manager owning the murder process lifecycle."""

    def __init__(self, config: Config, repo_root: Path) -> None:
        self.config = config
        self.repo_root = repo_root
        self.db: sqlite3.Connection | None = None
        self.bus: Bus | None = None
        self.run_id: str | None = None
        self._agents = AgentRegistry()
        self.agents = self._agents
        self.event_sink: AgentEventSink = LoggingAgentEventSink()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._shutdown = asyncio.Event()
        self.harness_versions = HarnessVersionRegistry()
        self._external_stop = asyncio.Event()
        self._lock_fd: int | None = None
        self._sync: FilesystemSyncSupervisor | None = None
        self.plan_sync: PlanSync | None = None
        self.note_sync: NoteSync | None = None
        self.notetaker_context_sync: NotetakerContextSync | None = None
        self.ticket_sync: TicketSync | None = None
        self.documents = DocumentAccess(self.repo_root)

    async def __aenter__(self) -> Runtime:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        await self.stop()

    async def start(self) -> None:
        self._shutdown.clear()
        self._external_stop.clear()
        self._lock_fd = acquire_flock(lock_path(self.repo_root))
        self.db = _db_connect(db_path(self.repo_root))
        _db_init_schema(self.db)
        live_sessions = set(await tmux.list_sessions())
        report = reconcile_agents_vs_tmux(self.db, live_sessions)
        if report:
            logging.getLogger(__name__).info("startup reconcile: %s", report.summary())
        stale_count = mark_stale_conversations(self.db)
        if stale_count:
            logging.getLogger(__name__).info(
                "startup: marked %d in_progress conversation(s) stale", stale_count
            )
        self.run_id = allocate_run_id(self.repo_root)
        snap = json.dumps(self.config.model_dump(mode="json"), default=str)
        _db_insert_run(self.db, self.run_id, snap)
        self.bus = Bus(self.run_id, self.db)
        self._sync = FilesystemSyncSupervisor.attach(self.repo_root, self.db)
        self.plan_sync = self._sync.plan_sync
        self.note_sync = self._sync.note_sync
        self.notetaker_context_sync = self._sync.notetaker_context_sync
        self.ticket_sync = self._sync.ticket_sync
        self.documents = DocumentAccess(
            self.repo_root,
            self.db,
            plan_sync=self.plan_sync,
            note_sync=self.note_sync,
        )
        await self._sync.reconcile_all()
        self._tasks.update(self._sync.spawn_tasks())

    async def stop(self) -> None:
        self._shutdown.set()
        if self._sync is not None:
            await self._sync.shutdown(self._tasks)
        for t in list(self._tasks.values()):
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._tasks.clear()
        graceful = self._external_stop.is_set()
        await shutdown_live_agents(self._agents, graceful=graceful)
        if self.run_id and self.db is not None:
            _db_end_run(self.db, self.run_id)
        if self.db is not None:
            self.db.close()
            self.db = None
        self._sync = None
        self.plan_sync = None
        self.note_sync = None
        self.notetaker_context_sync = None
        self.ticket_sync = None
        self.documents = DocumentAccess(self.repo_root)
        self.bus = None
        self.run_id = None
        if self._lock_fd is not None:
            release_flock(self._lock_fd)
            self._lock_fd = None
            with contextlib.suppress(FileNotFoundError, OSError):
                lock_path(self.repo_root).unlink()

    def sync_agent(self, agent: LifecycleParticipant) -> None:
        """Persist current agent fields to SQLite."""
        if self.db is None:
            return
        worktree_path = getattr(agent, "worktree_path", None)
        _db_upsert_agent(
            self.db,
            agent_id=agent.id,
            role=agent.role.value,
            ticket_id=agent.ticket_id,
            session=agent.session,
            harness=getattr(getattr(agent, "harness", None), "kind", None),
            model=getattr(agent, "startup_model", None),
            status=agent.status.value,
            start_commit=getattr(agent, "start_commit", None),
            worktree_path=str(worktree_path) if worktree_path is not None else None,
            pid=None,
        )

    def register_agent(self, agent: LifecycleParticipant) -> None:
        self._agents.register(agent, persist=self.sync_agent)

    def get_agent(self, agent_id: str) -> LifecycleParticipant | None:
        return self._agents.get_agent(agent_id)

    def get_crow(self, ticket_id: str) -> LifecycleParticipant | None:
        return self._agents.get_crow(ticket_id)

    def get_crow_handler(self, ticket_id: str) -> LifecycleParticipant | None:
        return self._agents.get_crow_handler(ticket_id)

    async def reap(self, agent_id: str) -> None:
        await self._agents.reap(
            agent_id,
            tasks=self._tasks,
            db=self.db,
            set_dead=_db_set_agent_status,
        )

    async def supervise(self, agent_id: str) -> None:
        """Restart policy placeholder — daemons own their poll loops."""
        return None

    @asynccontextmanager
    async def subscription(
        self,
        handler: Handler,
        filter: EventFilter | None = None,
    ) -> AsyncGenerator[SubscriptionHandle, None]:
        if self.bus is None:
            raise RuntimeError("Runtime not started (no bus)")
        handle = self.bus.subscribe(handler, filter)
        try:
            yield handle
        finally:
            handle.cancel()

    async def run_until_signal(self) -> None:
        """Block until SIGINT/SIGTERM (Linux/macOS). Used after CLI kickoff."""
        loop = asyncio.get_running_loop()

        def _wake() -> None:
            self._external_stop.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, _wake)
        await self._external_stop.wait()

    async def reconcile_plan(self, name: str) -> None:
        await self.documents.reconcile_plan(name)

    async def open_plan_in_editor(self, name: str, preferred_editor: str | None = None) -> int:
        return await self.documents.open_plan_in_editor(name, preferred_editor)

    async def open_note_in_editor(self, name: str, preferred_editor: str | None = None) -> int:
        return await self.documents.open_note_in_editor(name, preferred_editor)

    async def open_report_in_editor(self, name: str, preferred_editor: str | None = None) -> int:
        return await self.documents.open_report_in_editor(name, preferred_editor)

    def open_editor_blocking(self, path: Path, preferred_editor: str | None = None) -> int:
        return self.documents.open_editor_blocking(path, preferred_editor)

    def plan_path_for(self, name: str) -> Path:
        return self.documents.plan_path_for(name)

    def note_path_for(self, name: str) -> Path:
        return self.documents.note_path_for(name)

    def report_path_for(self, name: str) -> Path:
        return self.documents.report_path_for(name)
