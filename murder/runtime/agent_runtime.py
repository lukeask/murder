"""Complete agent mutation and lifetime owner (Phase 2 AgentRuntime)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from murder.app.service.agent_registry import AgentRegistry
from murder.config import Config
from murder.observability.advanced_log import AdvancedLogBase, StateMutationRecord
from murder.roster.service import RosterService
from murder.runtime.agents.types import AgentStatus
from murder.runtime.agents.verified_control import VerifiedControlFactory
from murder.runtime.orchestration.events import AgentLifecycleEvent, StatusChangeEvent
from murder.runtime.orchestration.ports import CommandSubmitter, OrchestrationEventSink
from murder.state.persistence.agents import rename_agent as _db_rename_agent
from murder.state.persistence.connection import RepoDb

if TYPE_CHECKING:
    from murder.runtime.agents.base import LifecycleParticipant
    from murder.runtime.orchestration.structured_decisions import StructuredDecisionRouter
    from murder.runtime.sessions.service import SessionService

LOGGER = logging.getLogger(__name__)

CrowAskRouter = Callable[[str | None, str, str], Awaitable[None]]


class CrowAskRouterSlot:
    """Always-present crow-ask route; bound after Orchestrator exists."""

    def __init__(self) -> None:
        self._route: CrowAskRouter | None = None

    def bind(self, route: CrowAskRouter) -> None:
        self._route = route

    def clear(self) -> None:
        self._route = None

    @property
    def bound(self) -> bool:
        return self._route is not None

    async def __call__(
        self, ticket_id: str | None, ask: str, crow_session: str
    ) -> None:
        route = self._route
        if route is None:
            return
        await route(ticket_id, ask, crow_session)


class AgentRuntime:
    """Owns live agent indexes, durable roster projection, and participant lifetime."""

    def __init__(
        self,
        *,
        db: RepoDb,
        config: Config,
        repo_root: Path,
        roster: RosterService,
        events: OrchestrationEventSink,
        run_id: str,
        advanced_log: AdvancedLogBase,
        preserve_tmux_on_close: Callable[[], bool],
        verified_control_factory: VerifiedControlFactory,
        command_submitter: CommandSubmitter,
        sessions: SessionService,
        lifecycle_events_enabled: bool = True,
    ) -> None:
        self._registry = AgentRegistry()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._emit_tasks: set[asyncio.Task[None]] = set()
        self._closed = False
        self._db = db
        self._roster = roster
        self._events = events
        self._run_id = run_id
        self._advanced_log = advanced_log
        self._verified_control_factory = verified_control_factory
        self._preserve_tmux_on_close = preserve_tmux_on_close
        self._lifecycle_events_enabled = lifecycle_events_enabled
        # Slot is non-null from construction; ServiceHost binds the orchestrator
        # route after Orchestrator exists (chicken-egg with agents dep).
        self.crow_ask_router = CrowAskRouterSlot()
        # Bound by ServiceHost after StructuredDecisionRouter exists (needs agents.find).
        # Projection must not run before that assignment.
        self.structured_decisions: StructuredDecisionRouter | None = None
        # Agent conversation / daemon code still needs process bindings. These are
        # NOT for Orchestrator reach-in — Orchestrator receives db/events/run_id
        # as its own constructor deps. Agents store this AgentRuntime as their
        # lifecycle dependency and use these for conversation + command paths.
        self.db = db
        self.config = config
        self.repo_root = repo_root
        self.orchestration_events = events
        self.run_id = run_id
        self.command_submitter = command_submitter
        self.sessions = sessions

    @classmethod
    @asynccontextmanager
    async def open(
        cls,
        *,
        db: RepoDb,
        config: Config,
        repo_root: Path,
        roster: RosterService,
        events: OrchestrationEventSink,
        run_id: str,
        advanced_log: AdvancedLogBase,
        preserve_tmux_on_close: Callable[[], bool],
        verified_control_factory: VerifiedControlFactory,
        command_submitter: CommandSubmitter,
        sessions: SessionService,
        lifecycle_events_enabled: bool = True,
    ) -> AsyncIterator[AgentRuntime]:
        runtime = cls(
            db=db,
            config=config,
            repo_root=repo_root,
            roster=roster,
            events=events,
            run_id=run_id,
            advanced_log=advanced_log,
            preserve_tmux_on_close=preserve_tmux_on_close,
            verified_control_factory=verified_control_factory,
            command_submitter=command_submitter,
            sessions=sessions,
            lifecycle_events_enabled=lifecycle_events_enabled,
        )
        try:
            yield runtime
        finally:
            await runtime.close()

    def register(self, agent: LifecycleParticipant) -> None:
        if self._closed:
            raise RuntimeError("AgentRuntime is closed")
        self._registry.add(agent)
        try:
            self.record(agent)
        except BaseException:
            self._registry.remove(agent.id)
            raise
        self._schedule_lifecycle(
            op="register",
            agent_id=agent.id,
            details={
                "role": getattr(getattr(agent, "role", None), "value", None),
                "ticket_id": agent.ticket_id,
            },
        )

    def record(self, agent: LifecycleParticipant) -> None:
        """Upsert roster projection for mutable agent state without identity change.

        Persistence is required. Advanced logging is observational and must not
        fail the caller after a successful roster sync.
        """
        worktree_path = getattr(agent, "worktree_path", None)
        self._roster.sync_agent(
            self._db,
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
        try:
            self._advanced_log.record_state_mutation(
                StateMutationRecord(
                    entity="agent",
                    agent_id=agent.id,
                    role=agent.role.value,
                    ticket_id=agent.ticket_id,
                    session=agent.session,
                    status=agent.status.value,
                    harness=getattr(getattr(agent, "harness", None), "kind", None),
                    model=getattr(agent, "startup_model", None),
                    worktree_path=str(worktree_path) if worktree_path is not None else None,
                )
            )
        except Exception:
            LOGGER.warning(
                "advanced log state mutation failed for agent %s",
                agent.id,
                exc_info=True,
            )

    async def transition(
        self,
        agent: LifecycleParticipant,
        *,
        from_status: AgentStatus | str | None,
        to_status: AgentStatus,
        reason: str | None = None,
    ) -> None:
        """Set status, persist, and publish StatusChangeEvent.

        Persistence is required. Orchestration publication is required while the
        service is running. Advanced logging is observational (via record).
        """
        del reason  # reserved for future forensic detail
        previous = agent.status
        if from_status is not None:
            expected = (
                from_status
                if isinstance(from_status, AgentStatus)
                else AgentStatus(str(from_status))
            )
            if previous is not expected:
                LOGGER.debug(
                    "agent %s transition from_status mismatch: have %s expected %s",
                    agent.id,
                    previous,
                    expected,
                )
        agent.status = to_status
        self.record(agent)
        if self._closed:
            return
        await self._events.publish(
            StatusChangeEvent(
                run_id=self._run_id,
                agent_id=agent.id,
                role=agent.role,
                ticket_id=agent.ticket_id,
                entity="agent",
                entity_id=agent.id,
                from_status=previous.value,
                to_status=to_status.value,
            )
        )

    def rename(
        self,
        old_agent_id: str,
        new_agent_id: str,
    ) -> LifecycleParticipant:
        if self._closed:
            raise RuntimeError("AgentRuntime is closed")
        agent = self._registry.get(old_agent_id)
        if agent is None:
            raise KeyError(old_agent_id)
        if old_agent_id != new_agent_id and self._registry.get(new_agent_id) is not None:
            raise ValueError(f"agent already registered: {new_agent_id}")

        old_session = agent.session
        moved_task: asyncio.Task[None] | None = None
        if old_agent_id != new_agent_id:
            moved_task = self._tasks.pop(old_agent_id, None)
            if moved_task is not None:
                # Match attach_task: cancel any stale entry under the new id.
                existing = self._tasks.get(new_agent_id)
                if existing is not None and not existing.done():
                    existing.cancel()
                self._tasks[new_agent_id] = moved_task

        self._registry.rename(old_agent_id, new_agent_id)
        # RepoDb opens with isolation_level=None (autocommit). ``with self._db``
        # only commits/rollbacks an already-open transaction — it does not start
        # one — so rename + record need an explicit BEGIN for atomicity.
        conn = self._db.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            _db_rename_agent(
                self._db,
                old_agent_id,
                new_agent_id,
                session=old_session,
            )
            self.record(agent)
            conn.commit()
        except BaseException:
            with contextlib.suppress(Exception):
                conn.rollback()
            # Restore old identity and indexes before re-raising.
            with contextlib.suppress(Exception):
                self._registry.rename(new_agent_id, old_agent_id)
            if moved_task is not None:
                self._tasks.pop(new_agent_id, None)
                self._tasks[old_agent_id] = moved_task
            raise

        self._schedule_lifecycle(
            op="rename",
            agent_id=new_agent_id,
            details={
                "old_agent_id": old_agent_id,
                "role": getattr(getattr(agent, "role", None), "value", None),
                "ticket_id": agent.ticket_id,
            },
        )
        return agent

    async def reap(self, agent_id: str) -> None:
        agent = self._registry.remove(agent_id)
        if agent is None:
            return
        task = self._tasks.pop(agent_id, None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        with contextlib.suppress(Exception):
            await agent.stop()
        self._roster.set_agent_status(self._db, agent_id, AgentStatus.DEAD.value)

    def find(self, agent_id: str) -> LifecycleParticipant | None:
        return self._registry.get(agent_id)

    def find_crow(self, ticket_id: str) -> LifecycleParticipant | None:
        return self._registry.get_crow(ticket_id)

    def find_crow_handler(self, ticket_id: str) -> LifecycleParticipant | None:
        return self._registry.get_crow_handler(ticket_id)

    def all(self) -> tuple[LifecycleParticipant, ...]:
        return self._registry.all()

    def heartbeat(self, agent_id: str, *, invalidate: bool) -> None:
        self._roster.heartbeat_agent(self._db, agent_id, invalidate=invalidate)

    def attach_task(self, agent_id: str, task: asyncio.Task[None]) -> None:
        existing = self._tasks.get(agent_id)
        if existing is not None and not existing.done():
            existing.cancel()
        self._tasks[agent_id] = task

    async def initialize_verified_control(self, agent: LifecycleParticipant) -> None:
        factory = self._verified_control_factory
        factory.sessions = self.sessions
        await factory.initialize(agent)  # type: ignore[arg-type]

    def emit_lifecycle(
        self,
        *,
        op: str,
        agent_id: str,
        details: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> None:
        """Schedule a forensic AgentLifecycleEvent (force-stop and similar)."""
        self._schedule_lifecycle(op=op, agent_id=agent_id, details=details, reason=reason)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._emit_tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.gather(*list(self._emit_tasks), return_exceptions=True)
            self._emit_tasks.clear()
        for task in list(self._tasks.values()):
            task.cancel()
        if self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)
            self._tasks.clear()

        graceful = self._preserve_tmux_on_close()
        terminal_statuses = {AgentStatus.DONE, AgentStatus.FAILED, AgentStatus.DEAD}

        async def _stop_one(agent: LifecycleParticipant) -> None:
            with contextlib.suppress(Exception):
                await agent.stop(
                    failed=agent.status not in terminal_statuses,
                    kill_session=not graceful,
                )

        await asyncio.gather(*(_stop_one(agent) for agent in self._registry.all()))
        self._registry.clear()

    def _schedule_lifecycle(
        self,
        *,
        op: str,
        agent_id: str,
        details: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> None:
        if self._closed or not self._lifecycle_events_enabled:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(
            self._events.publish(
                AgentLifecycleEvent(
                    run_id=self._run_id,
                    agent_id=agent_id,
                    op=op,  # type: ignore[arg-type]
                    details=details or {},
                    reason=reason,
                )
            )
        )
        self._emit_tasks.add(task)
        task.add_done_callback(self._emit_tasks.discard)


__all__ = ["AgentRuntime", "CrowAskRouterSlot"]
