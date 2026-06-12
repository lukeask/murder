"""Orchestration: spawn/kill agents; ready computation."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

_TNUM_RE = re.compile(r"^t(\d+)$")

LOGGER = logging.getLogger(__name__)

from murder.state.persistence.tickets import (
    get_ticket as _db_get_ticket,
    compute_ready as _db_compute_ready,
)
from murder.state.persistence.agents import (
    upsert_agent as _db_upsert_agent,
    get_active_agent_by_role as _db_get_active_agent_by_role,
    set_agent_status as _db_set_agent_status,
)
from murder.runtime.agents.base import AgentRole, AgentStatus
from murder.runtime.agents.crow_handler import CrowHandler
from murder.runtime.agents.planning_handler import PlanningHandler
from murder.bus import Entity, StatusChangeEvent, TicketStatus
from murder.llm.clients import resolve_role_client
from murder.config import (
    Config,
    resolve_default_crow_harness,
    resolve_default_crow_startup_effort,
    resolve_default_crow_startup_model,
)
from murder.llm.harnesses import get as get_harness
from murder.llm.harnesses.harnesses_doc import write_harnesses_doc
from murder.state.storage.paths import tickets_dir
from murder.state.storage.worktrees import (
    ensure_named_worktree,
)
from murder.runtime.terminal import tmux
from murder.runtime.terminal.session_names import format_session_name
from murder.work.tickets import lifecycle

from murder.runtime.agents.crow import CrowAgent
from murder.runtime.agents.runner import spawn_agent
from murder.runtime.agents.sessions import AgentScope, AgentSpec
from murder.llm.harnesses.models import HarnessStartSpec
from murder.verdict.completion import CheckRegistry, CompletionCoordinator
from murder.llm.harnesses import capabilities_for
from murder.runtime.orchestration.brief import BriefContext, assembler_for
from murder.runtime.orchestration.ticket_ops import TicketOps
from murder.runtime.orchestration.note_ops import NoteOps
from murder.runtime.orchestration.plan_ops import PlanOps
from murder.runtime.orchestration.agent_ops import AgentOps
from murder.app.service.runtime_scope import OrchestratorHost

from murder.verdict.escalations.service import EscalationService
from .outcome import TicketOutcomeService


def _get_plan_for_ticket(conn: sqlite3.Connection, ticket_id: str) -> str | None:
    """Return the plan_name for a ticket, or None if not in any plan."""
    has_plan_tickets = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'plan_tickets'"
    ).fetchone()
    table = "plan_tickets" if has_plan_tickets is not None else "plan_related_tickets"
    row = conn.execute(
        f"SELECT plan_name FROM {table} WHERE ticket_id = ? LIMIT 1",
        (ticket_id,),
    ).fetchone()
    return str(row["plan_name"]) if row else None


def _rogue_slug(name: str | None) -> str:
    if name and name.strip():
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip()).strip("-").lower()
        if slug:
            return slug[:32]
    return uuid4().hex[:8]


def _harness_prefix(harness_kind: str) -> str:
    """Short harness label for rogue agent IDs (e.g. 'claude', 'codex')."""
    first_word = harness_kind.split("_")[0].split("-")[0].lower()
    return first_word[:8] or "rogue"


# Re-exported from the pure-util module so existing backend callers keep
# importing it from here; renderer-agnostic clients import the util directly.
from murder.runtime.orchestration.agent_ids import is_rogue_agent_id  # noqa: E402, F401


def _codex_startup_model_degraded_ok(
    harness_kind: str,
    startup_model: str | None,
    harness_adapter: Any,
    message: str,
) -> bool:
    if harness_kind != "codex" or startup_model is None:
        return False
    known_startup_models = {
        model_id
        for model_id, _label in getattr(harness_adapter, "available_startup_models", ())
    }
    if startup_model not in known_startup_models:
        return False
    msg = message.lower()
    return "failed to select runtime model" in msg or "not idle in time" in msg


class Orchestrator:
    def __init__(self, rt: OrchestratorHost) -> None:
        self.rt = rt
        self._question_listener: Any = None
        self._planner_spawn_locks: dict[str, asyncio.Lock] = {}
        self.completion_coordinator = CompletionCoordinator(
            rt,
            CheckRegistry(),
            ensure_planning_agent=self.ensure_planning_agent,
        )
        # Concern services. Cross-concern hooks are injected as late-bound
        # closures over ``self`` so a facade-level monkeypatch (the test
        # convention) is honored at call time rather than frozen at construction.
        self.tickets = TicketOps(rt, emit_ticket_status=self._emit_ticket_status)
        self.notes = NoteOps(rt)
        self.plans = PlanOps(
            rt,
            send_agent_message=lambda *a, **k: self.send_agent_message(*a, **k),
            planner_spawn_locks=self._planner_spawn_locks,
        )
        self.agent_ops = AgentOps(
            rt,
            ensure_planning_agent=lambda *a, **k: self.ensure_planning_agent(*a, **k),
            ensure_collaborator=lambda *a, **k: self.ensure_collaborator(*a, **k),
            reap_ticket_crow_agents=lambda *a, **k: self.tickets._reap_ticket_crow_agents(
                *a, **k
            ),
            rogue_slug=_rogue_slug,
            agent_is_live=lambda agent: self._agent_is_live(agent),
        )

    def _escalations(self) -> EscalationService:
        assert self.rt.db is not None
        return EscalationService(
            conn=self.rt.db,
            repo_root=self.rt.repo_root,
            bus=self.rt.bus,
            run_id=self.rt.run_id,
            agent_id="orchestrator",
            role=AgentRole.COLLABORATOR,
        )

    def _outcomes(self) -> TicketOutcomeService:
        assert self.rt.db is not None
        return TicketOutcomeService(
            conn=self.rt.db,
            repo_root=self.rt.repo_root,
            escalations=self._escalations(),
            emit_status=self._emit_ticket_status,
            emit_snapshot=lambda tid: self.rt.publish_snapshot(Entity.TICKET, tid),
        )

    async def kickoff_ready(self, only: str | None = None) -> list[str]:
        assert self.rt.db is not None and self.rt.bus is not None and self.rt.run_id is not None
        conn = self.rt.db
        ready = _db_compute_ready(conn)
        if only is not None:
            if only not in ready:
                return []
            to_start = [only]
        else:
            to_start = list(ready)
        kicked: list[str] = []
        for tid in to_start:
            row = _db_get_ticket(conn, tid)
            if row is None:
                continue
            ticket_status = str(row.get("status") or "")
            running = conn.execute(
                "SELECT 1 FROM agents WHERE ticket_id = ? AND role IN ('crow','crow_handler') "
                "AND status IN ('running','idle')",
                (tid,),
            ).fetchone()
            if running is not None:
                if ticket_status == TicketStatus.IN_PROGRESS.value:
                    continue
                await self._reap_ticket_crow_agents(tid)
            prev = lifecycle.transition(conn, tid, TicketStatus.IN_PROGRESS)
            await self._emit_ticket_status(tid, prev, TicketStatus.IN_PROGRESS.value)
            try:
                await self.spawn_crow(tid)
            except Exception as e:
                reason = f"Failed to start crow for {tid}: {e}"
                crow = self.rt.get_crow(tid)
                if crow is not None:
                    crow.status = AgentStatus.FAILED
                    self.rt.sync_agent(crow)
                else:
                    _db_upsert_agent(
                        conn,
                        agent_id=f"crow-{tid}",
                        role=AgentRole.CROW.value,
                        ticket_id=tid,
                        session=format_session_name(self.rt, "crow", f"_{tid}"),
                        status=AgentStatus.FAILED.value,
                    )
                await self._fail_ticket(tid, reason)
                continue
            crow = self.rt.get_crow(tid)
            assert crow is not None
            await self.spawn_crow_handler(tid, crow.session)
            from murder.state.persistence.tickets import get_ticket_status

            if get_ticket_status(conn, tid) != TicketStatus.IN_PROGRESS.value:
                await self._fail_ticket(
                    tid,
                    f"kickoff status drift: expected in_progress, got {get_ticket_status(conn, tid)}",
                )
                continue
            kicked.append(tid)
        return kicked

    def next_ticket_id(self) -> str:
        return self.tickets.next_ticket_id()

    def ticket_exists(self, handle: str) -> bool:
        return self.tickets.ticket_exists(handle)

    def quick_create_ticket(self, title: str) -> dict[str, Any]:
        return self.tickets.quick_create_ticket(title)

    async def quick_kick_ticket(self, title: str) -> dict[str, Any]:
        """Create a ticket, insert it into the DB as PLANNED, and immediately kick it."""
        created = self.quick_create_ticket(title)
        ticket_id = str(created["ticket_id"])
        kicked = await self.kickoff_ready(only=ticket_id)
        return {"handled": True, "ticket_id": ticket_id, "title": title, "kicked": kicked}

    async def _emit_ticket_status(
        self, ticket_id: str, from_status: str | TicketStatus, to_status: str
    ) -> None:
        if self.rt.bus is None or self.rt.run_id is None:
            return
        from_s = from_status.value if isinstance(from_status, TicketStatus) else from_status
        await self.rt.bus.publish(
            StatusChangeEvent(
                run_id=self.rt.run_id,
                agent_id="orchestrator",
                role=AgentRole.COLLABORATOR,
                ticket_id=ticket_id,
                entity="ticket",
                entity_id=ticket_id,
                from_status=from_s,
                to_status=to_status,
            )
        )
        # F1: the status-transition choke point also emits the key-only
        # state.snapshot{ticket}. ~5 sites funnel here (kickoff / retry / force /
        # carve-ready, and outcome.fail_ticket via the injected emit_status), so
        # the snapshot rides alongside the existing typed event in one place.
        await self.rt.publish_snapshot(Entity.TICKET, ticket_id)

    async def _fail_ticket(self, ticket_id: str, reason: str) -> None:
        await self._outcomes().fail_ticket(ticket_id, reason)

    async def spawn_crow(self, ticket_id: str) -> str:
        row = _db_get_ticket(self.rt.db, ticket_id)
        if row is None:
            raise KeyError(ticket_id)
        harness_kind = resolve_default_crow_harness(self.rt.config.default_crow, row)
        startup_model = resolve_default_crow_startup_model(
            self.rt.config.default_crow, row, harness_kind
        )
        startup_effort = resolve_default_crow_startup_effort(self.rt.config.default_crow, row)
        worktree_name = row.get("worktree")
        worktree_path: str | None = None
        if isinstance(worktree_name, str) and worktree_name.strip():
            worktree = await ensure_named_worktree(
                self.rt.repo_root,
                worktree_name.strip(),
                category="crow",
            )
            worktree_path = str(worktree.path)
        additional_workspace_dirs: tuple[str, ...] = ()
        if harness_kind == "codex" and worktree_path is not None:
            additional_workspace_dirs = (str(tickets_dir(self.rt.repo_root).resolve()),)
        ctx = BriefContext(
            role=AgentRole.CROW,
            repo_root=self.rt.repo_root,
            caps=capabilities_for(harness_kind),
            harness_name=harness_kind,
            model=None,
            ticket=dict(row),
        )
        brief = assembler_for(ctx).build(ctx)
        spec = AgentSpec(
            role=AgentRole.CROW,
            scope=AgentScope(ticket_id=ticket_id, worktree_path=worktree_path),
            harness=harness_kind,
            model=startup_model,
            effort=startup_effort,
            startup_prompt=brief,
            additional_workspace_dirs=additional_workspace_dirs,
        )
        handle = await spawn_agent(spec, rt=self.rt, event_sink=self.rt.event_sink)
        return handle.session_name

    async def reattach_crow(self, ticket_id: str, crow_session: str) -> None:
        """Rehydrate an in-memory CrowAgent around an already-live tmux session.

        Used on startup recovery: the crow's tmux session survived a service
        restart but its in-memory agent/handler did not, so DONE would never be
        consumed. We bind a fresh CrowAgent to the live pane (no harness start,
        no prompt) and spawn a fresh handler. Transcript projection restarts from
        the current scrollback.
        """
        row = _db_get_ticket(self.rt.db, ticket_id)
        if row is None:
            raise KeyError(ticket_id)
        harness_kind = resolve_default_crow_harness(self.rt.config.default_crow, row)
        startup_model = resolve_default_crow_startup_model(
            self.rt.config.default_crow, row, harness_kind
        )
        startup_effort = resolve_default_crow_startup_effort(self.rt.config.default_crow, row)
        harness = get_harness(
            harness_kind,
            startup_model=startup_model,
            startup_effort=startup_effort,
        )

        repo_root = self.rt.repo_root
        worktree_path: Path | None = None
        worktree_name = row.get("worktree")
        if isinstance(worktree_name, str) and worktree_name.strip():
            worktree = await ensure_named_worktree(
                self.rt.repo_root,
                worktree_name.strip(),
                category="crow",
            )
            repo_root = worktree.path
            worktree_path = worktree.path

        agent = CrowAgent(
            agent_id=f"crow-{ticket_id}",
            ticket_id=ticket_id,
            session=crow_session,
            harness=harness,
            repo_root=repo_root,
            startup_model=startup_model,
            startup_effort=startup_effort,
            worktree_path=worktree_path,
            runtime=self.rt,
        )
        self.rt.register_agent(agent)
        agent.status = AgentStatus.RUNNING
        self.rt.sync_agent(agent)
        # Fresh accumulator; reattach resumes transcript projection from the
        # current pane scrollback rather than the original startup state.
        agent.start_conversation()
        await self.rt.publish_snapshot(Entity.AGENT, agent.id)
        await self.spawn_crow_handler(ticket_id, crow_session)

    async def spawn_crow_handler(self, ticket_id: str, crow_session: str) -> str:
        row = _db_get_ticket(self.rt.db, ticket_id)
        if row is None:
            raise KeyError(ticket_id)
        harness_kind = resolve_default_crow_harness(self.rt.config.default_crow, row)
        startup_model = resolve_default_crow_startup_model(
            self.rt.config.default_crow, row, harness_kind
        )
        startup_effort = resolve_default_crow_startup_effort(self.rt.config.default_crow, row)
        harness = get_harness(
            harness_kind,
            startup_model=startup_model,
            startup_effort=startup_effort,
        )
        session = format_session_name(self.rt, "crow_handler", f"_{ticket_id}")
        client = resolve_role_client(self.rt.config.crow_handler)
        crow_agent = self.rt.get_crow(ticket_id)
        worktree_path = getattr(crow_agent, "worktree_path", None) if crow_agent else None
        handler = CrowHandler(
            agent_id=f"crow_handler-{ticket_id}",
            ticket_id=ticket_id,
            session=session,
            crow_session=crow_session,
            harness=harness,
            config=self.rt.config.crow_handler,
            repo_root=self.rt.repo_root,
            workspace_root=worktree_path,
            runtime=self.rt,
            outcome=self._outcomes(),
            coordinator=self.completion_coordinator,
            client=client,
        )
        self.rt.register_agent(handler)
        await handler.start("", {})
        return handler.id

    async def spawn_planning_handler(self, plan_name: str, planner_session: str) -> str:
        """Spawn a PlanningHandler coroutine for the given planner session."""
        handler_id = f"planning_handler-{plan_name}"
        cfg = self.rt.config.planner
        harness = get_harness(cfg.harness, startup_model=cfg.startup_model)
        log_session = format_session_name(self.rt, "planning_handler", f"_{plan_name}")
        handler = PlanningHandler(
            agent_id=handler_id,
            session=log_session,
            planner_session=planner_session,
            plan_name=plan_name,
            harness=harness,
            config=cfg,
            repo_root=self.rt.repo_root,
            runtime=self.rt,
        )
        self.rt.register_agent(handler)
        await handler.start("", {})
        return handler_id

    async def spawn_rogue(
        self,
        harness: str,
        model: str,
        effort: str | None = None,
        name: str | None = None,
        *,
        worktree_path: str | None = None,
        worktree_branch: str | None = None,
    ) -> str:
        """Start a ticketless crow session; inject model selection when supported."""
        harness_kind = harness.strip()
        if not harness_kind:
            raise ValueError("spawn_rogue requires harness")

        slug = _rogue_slug(name)
        prefix = _harness_prefix(harness_kind)
        agent_id = f"{prefix}-rogue-{slug}"
        while self.rt.get_agent(agent_id) is not None:
            agent_id = f"{prefix}-rogue-{uuid4().hex[:8]}"

        session_name = format_session_name(self.rt, "crow", f"_{prefix}_rogue_{slug}")
        startup_model = model.strip() or None
        startup_effort = effort.strip() if isinstance(effort, str) and effort.strip() else None
        harness_adapter = get_harness(
            harness_kind,
            startup_model=startup_model,
            startup_effort=startup_effort,
        )

        cwd = self.rt.repo_root
        resolved_worktree: Path | None = None
        if isinstance(worktree_branch, str) and worktree_branch.strip():
            ref = await ensure_named_worktree(
                self.rt.repo_root,
                worktree_branch.strip(),
                category="rogue",
            )
            cwd = ref.path
            resolved_worktree = ref.path
        elif isinstance(worktree_path, str) and worktree_path.strip():
            path = Path(worktree_path.strip())
            if not path.is_absolute():
                path = self.rt.repo_root / path
            cwd = path
            resolved_worktree = path

        agent = CrowAgent(
            agent_id=agent_id,
            ticket_id=None,
            session=session_name,
            harness=harness_adapter,
            repo_root=cwd,
            startup_model=startup_model,
            startup_effort=startup_effort,
            worktree_path=resolved_worktree,
            runtime=self.rt,
        )

        self.rt.register_agent(agent)
        start_spec = HarnessStartSpec(
            cwd=cwd,
            startup_model=startup_model,
            startup_effort=startup_effort,
        )
        try:
            start_result = await agent.harness_session.start(start_spec)
            if not start_result.ok:
                message = start_result.message or "harness startup failed"
                if not _codex_startup_model_degraded_ok(
                    harness_kind, startup_model, harness_adapter, message
                ):
                    raise RuntimeError(message)
                agent.harness_session.require_first_send_idle_gate()
            agent.status = AgentStatus.RUNNING
            self.rt.sync_agent(agent)
            # Rogues bypass CrowAgent.start(), so kick off transcript projection
            # here: a fresh session ⇒ fresh accumulator + producer loop.
            agent.start_conversation()
            # Broadcast the freshly spawned agent so the Ink roster / Crows panel
            # picks it up immediately. Without this, rogue spawn emitted no `agent`
            # snapshot (unlike the ticket/plan/note handlers and the crow heartbeat
            # at crow_handler.py), so the new rogue only surfaced on the next
            # unrelated `agent` invalidation. The frontend also refreshes proactively
            # on spawn; this keeps the backend consistent with the event-driven design.
            await self.rt.publish_snapshot(Entity.AGENT, agent_id)
        except BaseException:
            await self.rt.reap(agent_id)
            raise
        return agent_id

    async def spawn_rogue_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        harness = payload.get("harness")
        model = payload.get("model")
        effort = payload.get("effort")
        name = payload.get("name")
        worktree_path = payload.get("worktree_path")
        worktree_branch = payload.get("worktree_branch")
        if not isinstance(harness, str) or not harness.strip():
            raise ValueError("crow.spawn_rogue requires harness")
        if not isinstance(model, str):
            raise ValueError("crow.spawn_rogue requires model")
        if effort is not None and not isinstance(effort, str):
            raise ValueError("crow.spawn_rogue effort must be a string")
        if worktree_path is not None and not isinstance(worktree_path, str):
            raise ValueError("crow.spawn_rogue worktree_path must be a string")
        if worktree_branch is not None and not isinstance(worktree_branch, str):
            raise ValueError("crow.spawn_rogue worktree_branch must be a string")
        rogue_name = name.strip() if isinstance(name, str) and name.strip() else None
        agent_id = await self.spawn_rogue(
            harness.strip(),
            model,
            effort,
            rogue_name,
            worktree_path=worktree_path.strip()
            if isinstance(worktree_path, str) and worktree_path.strip()
            else None,
            worktree_branch=worktree_branch.strip()
            if isinstance(worktree_branch, str) and worktree_branch.strip()
            else None,
        )
        return {"handled": True, "agent_id": agent_id}

    async def start_question_listener(self) -> None:
        """Subscribe to QuestionEvents on the bus and route to the per-plan planning agent.

        Fallback: if no plan is associated with the ticket, escalate to the user.
        """
        bus = self.rt.bus
        if bus is None:
            return

        async def _handle(event: Any) -> None:
            if getattr(event, "type", None) != "question":
                return
            ticket_id: str | None = getattr(event, "ticket_id", None)
            question: str = str(getattr(event, "question", ""))
            crow_session: str = str(getattr(event, "crow_session", ""))
            await self.route_crow_ask(ticket_id, question, crow_session)

        self._question_listener = bus.subscribe(_handle, None)

    async def route_crow_ask(
        self,
        ticket_id: str | None,
        ask: str,
        crow_session: str,
    ) -> None:
        """Route a crow ASK to the per-plan PlanningHandler, or escalate to user."""
        if ticket_id and self.rt.db is not None:
            plan_name = _get_plan_for_ticket(self.rt.db, ticket_id)
            if plan_name:
                try:
                    await self.ensure_planning_agent(plan_name)
                    handler = self.rt.get_agent(f"planning_handler-{plan_name}")
                    if isinstance(handler, PlanningHandler):
                        await handler.relay_ask(ticket_id, ask, crow_session)
                        return
                except Exception as exc:
                    LOGGER.warning("planner routing failed for %s: %s", plan_name, exc)
        reason = f"[crow ASK] {ask[:300]}"
        await self._escalations().escalate_to_user(reason, severity=2, ticket_id=ticket_id)

    async def ensure_planning_agent(self, plan_name: str) -> str:
        """Return the agent_id of a live planning agent for plan_name,
        spawning the agent + its handler if needed."""
        assert self.rt.db is not None
        agent_id = f"planner-{plan_name}"
        if plan_name not in self._planner_spawn_locks:
            self._planner_spawn_locks[plan_name] = asyncio.Lock()
        async with self._planner_spawn_locks[plan_name]:
            agent = self.rt.get_agent(agent_id)
            if agent is not None and await self._agent_is_live(agent):
                handler = self.rt.get_agent(f"planning_handler-{plan_name}")
                if not isinstance(handler, PlanningHandler):
                    await self.spawn_planning_handler(plan_name, agent.session)
                return agent_id
            cfg = self.rt.config.planner
            ctx = BriefContext(
                role=AgentRole.PLANNER,
                repo_root=self.rt.repo_root,
                caps=capabilities_for(cfg.harness),
                harness_name=cfg.harness,
                model=None,
                plan_name=plan_name,
            )
            startup_prompt = assembler_for(ctx).build(ctx)
            spec = AgentSpec(
                role=AgentRole.PLANNER,
                scope=AgentScope(plan_name=plan_name),
                harness=cfg.harness,
                model=cfg.startup_model,
                effort=cfg.startup_effort,
                startup_prompt=startup_prompt,
            )
            handle = await spawn_agent(spec, rt=self.rt, event_sink=self.rt.event_sink)
            # TODO: resumability — if a prior planner session exists with prior
            # transcript, future work will summarize via compact-style summary
            # and seed the new session. For now we always spawn fresh.
            await self.spawn_planning_handler(plan_name, handle.session_name)
            return agent_id

    async def _record_user_block(self, agent_id: str, text: str) -> None:
        await self.agent_ops._record_user_block(agent_id, text)

    async def send_agent_message(
        self,
        agent_id: str,
        message: str,
        ticket_id: str | None,
        *,
        spawn_if_needed: bool = True,
    ) -> dict[str, Any]:
        return await self.agent_ops.send_agent_message(
            agent_id, message, ticket_id, spawn_if_needed=spawn_if_needed
        )

    async def send_agent_key(
        self,
        agent_id: str | None,
        key: str,
        *,
        literal: bool = False,
        enter: bool = False,
        log_user_input: str | None = None,
    ) -> dict[str, Any]:
        return await self.agent_ops.send_agent_key(
            agent_id,
            key,
            literal=literal,
            enter=enter,
            log_user_input=log_user_input,
        )

    async def refresh_agent_transcript(self, agent_id: str) -> dict[str, Any]:
        return await self.agent_ops.refresh_agent_transcript(agent_id)

    async def stop_agent(self, agent_id: str) -> dict[str, Any]:
        return await self.agent_ops.stop_agent(agent_id)

    async def _force_stop_unregistered_agent(self, agent_id: str) -> dict[str, Any]:
        return await self.agent_ops._force_stop_unregistered_agent(agent_id)

    async def rename_rogue_agent(self, agent_id: str, name: str) -> dict[str, Any]:
        return await self.agent_ops.rename_rogue_agent(agent_id, name)

    async def interrupt_agent(self, agent_id: str) -> dict[str, Any]:
        return await self.agent_ops.interrupt_agent(agent_id)

    async def _agent_is_live(self, agent: Any) -> bool:
        return await self.agent_ops._agent_is_live(agent)

    async def scaffold_plan(self, name: str, body: str) -> dict[str, Any]:
        return await self.plans.scaffold_plan(name, body)

    async def rename_plan(self, old_name: str, new_name: str) -> dict[str, Any]:
        return await self.plans.rename_plan(old_name, new_name)

    async def deprecate_plan(self, name: str) -> dict[str, Any]:
        return await self.plans.deprecate_plan(name)

    async def _preflight_plan_runtime_rename(self, old_name: str, new_name: str) -> None:
        await self.plans._preflight_plan_runtime_rename(old_name, new_name)

    async def _retarget_plan_runtime(self, old_name: str, new_name: str) -> None:
        await self.plans._retarget_plan_runtime(old_name, new_name)
    async def ensure_collaborator(self) -> str:
        agent_id = _db_get_active_agent_by_role(self.rt.db, "collaborator")
        if agent_id:
            agent = self.rt.get_agent(agent_id)
            if agent is not None:
                if await agent.is_live():
                    return agent_id
                await self.rt.reap(agent_id)
            else:
                # Agent in DB but not in registry (e.g. service restart with
                # keep-sessions-alive). Kill the orphaned tmux session so the
                # upcoming create_session call doesn't raise "already exists".
                row = self.rt.db.execute(
                    "SELECT session FROM agents WHERE agent_id = ?", (agent_id,)
                ).fetchone()
                if row and row["session"] and await tmux.session_exists(row["session"]):
                    with contextlib.suppress(Exception):
                        await tmux.kill_session(row["session"])
                _db_set_agent_status(self.rt.db, agent_id, "dead")
        collab_cfg = self.rt.config.collaborator
        ctx = BriefContext(
            role=AgentRole.COLLABORATOR,
            repo_root=self.rt.repo_root,
            caps=capabilities_for(collab_cfg.harness),
            harness_name=collab_cfg.harness,
            model=None,
        )
        body = assembler_for(ctx).build(ctx)
        spec = AgentSpec(
            role=AgentRole.COLLABORATOR,
            scope=AgentScope(),
            harness=collab_cfg.harness,
            model=collab_cfg.startup_model,
            effort=collab_cfg.startup_effort,
            startup_prompt=body,
        )
        try:
            handle = await spawn_agent(spec, rt=self.rt, event_sink=self.rt.event_sink)
        except Exception as e:
            reason = f"Collaborator startup failed: {e}"
            await self._escalations().record_collaborator_startup_failure(reason)
            raise
        return handle.agent_id

    async def reconfigure_collaborator(self) -> dict[str, Any]:
        """Reload project config and restart the collaborator if its live harness changed."""
        from murder.llm.harnesses.model_cache import refresh_and_persist_harness_models

        new_config = Config.load(self.rt.repo_root)
        self.rt.config = new_config
        current_harness = new_config.collaborator.harness
        write_harnesses_doc(self.rt.repo_root)
        # Best-effort re-scrape: newly-enabled harnesses get discovered and
        # persisted. Failures are swallowed inside refresh_and_persist.
        try:
            await refresh_and_persist_harness_models(self.rt.repo_root, self.rt.db)
        except Exception:  # noqa: BLE001
            LOGGER.debug("model re-scrape after reconfigure_collaborator failed", exc_info=True)

        restarted = False
        agent_id = _db_get_active_agent_by_role(self.rt.db, "collaborator")
        live_harness: str | None = None
        if agent_id:
            agent = self.rt.get_agent(agent_id)
            if agent is not None:
                live_harness = str(getattr(getattr(agent, "harness", None), "kind", "") or "")
            else:
                row = self.rt.db.execute(
                    "SELECT harness FROM agents WHERE agent_id = ?",
                    (agent_id,),
                ).fetchone()
                live_harness = str(row["harness"]) if row and row["harness"] else None
        if live_harness == current_harness:
            return {
                "handled": True,
                "changed": False,
                "harness": current_harness,
            }

        if agent_id:
            agent = self.rt.get_agent(agent_id)
            if agent is not None:
                await agent.stop(failed=False, kill_session=True)
            await self.rt.reap(agent_id)
            try:
                await self.ensure_collaborator()
            except Exception as e:
                reason = f"Collaborator startup failed: {e}"
                await self._escalations().record_collaborator_startup_failure(reason)
                return {
                    "ok": False,
                    "changed": True,
                    "previous_harness": live_harness,
                    "harness": current_harness,
                    "restarted": False,
                    "error": str(e),
                }
            restarted = True
        return {
            "handled": True,
            "changed": True,
            "previous_harness": live_harness,
            "harness": current_harness,
            "restarted": restarted,
        }

    async def submit_notetaker_capture(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.notes.submit_notetaker_capture(payload)

    async def ensure_note(self, name: str) -> dict[str, Any]:
        return await self.notes.ensure_note(name)

    async def retire_note(self, name: str) -> dict[str, Any]:
        return await self.notes.retire_note(name)

    async def reopen_ticket(self, ticket_id: str) -> list[str]:
        return await self.tickets.reopen_ticket(ticket_id)

    async def retry_failed_ticket(self, ticket_id: str) -> dict[str, Any]:
        return await self.tickets.retry_failed_ticket(ticket_id)

    async def reset_crow(self, ticket_id: str) -> dict[str, Any]:
        return await self.tickets.reset_crow(ticket_id)

    async def set_schedule_at(self, ticket_id: str, schedule_at: str | None) -> dict[str, Any]:
        return await self.tickets.set_schedule_at(ticket_id, schedule_at)

    async def save_ticket_body(self, ticket_id: str, body: str) -> dict[str, Any]:
        return await self.tickets.save_ticket_body(ticket_id, body)

    async def schedule_ticket(self, ticket_id: str, duration: str) -> dict[str, Any]:
        return await self.tickets.schedule_ticket(ticket_id, duration)

    async def _derive_plan_name(self, body: str) -> str:
        return await self.plans._derive_plan_name(body)

    async def create_plan(
        self,
        plan_name: str | None,
        message: str,
        *,
        body: str | None = None,
        auto_name: bool = False,
    ) -> dict[str, Any]:
        return await self.plans.create_plan(
            plan_name, message, body=body, auto_name=auto_name
        )

    async def update_ticket_metadata(
        self, ticket_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return await self.tickets.update_ticket_metadata(ticket_id, payload)

    async def force_ticket_status(self, ticket_id: str, status: str) -> dict[str, Any]:
        return await self.tickets.force_ticket_status(ticket_id, status)

    async def _reap_ticket_crow_agents(self, ticket_id: str) -> None:
        await self.tickets._reap_ticket_crow_agents(ticket_id)

    async def apply_ticket_carve_ready(
        self, ticket_id: str, payload: dict[str, object]
    ) -> dict[str, object]:
        return await self.tickets.apply_ticket_carve_ready(ticket_id, payload)
