"""Long-lived async runtime + supervisor.

Owns the asyncio loop, the SQLite connection, the bus, and the lifecycle
of all agents. The TUI is one consumer in this same loop (D1: single
process). Daemons (CrowHandler, Sentinel) are coroutines spawned and supervised
here; their "tmux session" is a logfile being tailed for debug
visibility, not a real interactive session.

Process model rules:
- One murder process per repo. flock on `.agents/.lock` enforces.
- Graceful shutdown drains the bus, signals Crows, kills tmux sessions.
- Crash recovery: on startup, reconcile DB ↔ tmux ↔ filesystem before
  resuming.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import signal
import sqlite3
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from murder import db as dbmod
from murder.agents.base import AgentRole
from murder.bus import AgentStatus, Bus, EventFilter, SubscriptionHandle
from murder.plans.sync import PlanSync, choose_editor, open_editor
from murder.storage.filesystem import acquire_flock, release_flock
from murder.storage.paths import db_path, lock_path
from murder.storage.runs import allocate_run_id

if TYPE_CHECKING:
    from murder.agents.base import Agent
    from murder.config import Config

Handler = Callable[[Any], Awaitable[None]]


class Runtime:
    """Async context manager owning the murder process lifecycle."""

    def __init__(self, config: "Config", repo_root: Path) -> None:
        self.config = config
        self.repo_root = repo_root
        self.db: sqlite3.Connection | None = None
        self.bus: Bus | None = None
        self.run_id: str | None = None
        self._agents: dict[str, "Agent"] = {}
        self._crows: dict[str, "Agent"] = {}
        self._crow_handlers: dict[str, "Agent"] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._shutdown = asyncio.Event()
        self._external_stop = asyncio.Event()
        self._lock_fd: int | None = None
        self.plan_sync: PlanSync | None = None

    async def __aenter__(self) -> "Runtime":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        await self.stop()

    async def start(self) -> None:
        self._shutdown.clear()
        self._external_stop.clear()
        self._lock_fd = acquire_flock(lock_path(self.repo_root))
        self.db = dbmod.connect(db_path(self.repo_root))
        dbmod.init_schema(self.db)
        self.run_id = allocate_run_id(self.repo_root)
        snap = json.dumps(self.config.model_dump(mode="json"), default=str)
        dbmod.insert_run(self.db, self.run_id, snap)
        self.bus = Bus(self.run_id, self.db)
        self.plan_sync = PlanSync(self.repo_root, self.db)
        await self.plan_sync.reconcile_all()
        self._tasks["plan_sync"] = asyncio.create_task(self.plan_sync.run())

    async def stop(self) -> None:
        self._shutdown.set()
        for t in list(self._tasks.values()):
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._tasks.clear()
        if self.plan_sync is not None:
            with contextlib.suppress(Exception):
                await self.plan_sync.reconcile_all()
        terminal_statuses = {AgentStatus.DONE, AgentStatus.FAILED, AgentStatus.DEAD}
        for agent in list(self._agents.values()):
            with contextlib.suppress(Exception):
                await agent.stop(failed=agent.status not in terminal_statuses)
        self._agents.clear()
        self._crows.clear()
        self._crow_handlers.clear()
        if self.run_id and self.db is not None:
            dbmod.end_run(self.db, self.run_id)
        if self.db is not None:
            self.db.close()
            self.db = None
        self.plan_sync = None
        self.bus = None
        self.run_id = None
        if self._lock_fd is not None:
            release_flock(self._lock_fd)
            self._lock_fd = None
            with contextlib.suppress(FileNotFoundError, OSError):
                lock_path(self.repo_root).unlink()

    def sync_agent(self, agent: "Agent") -> None:
        """Persist current agent fields to SQLite."""
        if self.db is None:
            return
        dbmod.upsert_agent(
            self.db,
            agent_id=agent.id,
            role=agent.role.value,
            ticket_id=agent.ticket_id,
            session=agent.session,
            status=agent.status.value,
            start_commit=getattr(agent, "start_commit", None),
            pid=None,
        )

    def register_agent(self, agent: "Agent") -> None:
        self._agents[agent.id] = agent
        if agent.ticket_id is not None:
            if agent.role == AgentRole.CROW:
                self._crows[agent.ticket_id] = agent
            elif agent.role == AgentRole.CROW_HANDLER:
                self._crow_handlers[agent.ticket_id] = agent
        self.sync_agent(agent)

    def get_agent(self, agent_id: str) -> "Agent | None":
        return self._agents.get(agent_id)

    def get_crow(self, ticket_id: str) -> "Agent | None":
        return self._crows.get(ticket_id)

    def get_crow_handler(self, ticket_id: str) -> "Agent | None":
        return self._crow_handlers.get(ticket_id)

    async def reap(self, agent_id: str) -> None:
        agent = self._agents.pop(agent_id, None)
        if agent is None:
            return
        if agent.ticket_id is not None:
            self._crows.pop(agent.ticket_id, None)
            self._crow_handlers.pop(agent.ticket_id, None)
        t = self._tasks.pop(agent_id, None)
        if t is not None:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        with contextlib.suppress(Exception):
            await agent.stop()
        if self.db is not None:
            dbmod.set_agent_status(self.db, agent_id, AgentStatus.DEAD.value)

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
        if self.plan_sync is not None:
            await self.plan_sync.reconcile_name(name)

    async def open_plan_in_editor(self, name: str, preferred_editor: str | None = None) -> int:
        if self.plan_sync is None:
            raise RuntimeError("Runtime not started (no plan sync)")
        await self.plan_sync.reconcile_name(name)
        row = dbmod.get_plan_row(self.db, name) if self.db is not None else None
        path = (
            self.repo_root / row["materialized_path"]
            if row
            else self.repo_root / ".agents" / "plans" / f"{name}.md"
        )
        editor = choose_editor(preferred_editor)
        code = await open_editor(path, editor)
        await self.plan_sync.reconcile_name(name)
        return code
