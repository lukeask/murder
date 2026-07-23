"""Orchestration: spawn/kill agents; ready computation."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import sqlite3
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
from murder.runtime.agents.types import AgentRole, AgentStatus
from murder.runtime.agents.crow_handler import CrowHandler
from murder.runtime.agents.planning_handler import PlanningHandler
from murder.runtime.orchestration.events import StatusChangeEvent
from murder.work.tickets.status import TicketStatus
from murder.llm.direct import resolve_direct_role_client
from murder.config import (
    Config,
)
from murder.llm.harnesses import get as get_harness
from murder.llm.harnesses.harnesses_doc import write_harnesses_doc
from murder.runtime.terminal import tmux
from murder.runtime.terminal.session_names import format_session_name
from murder.work.tickets import lifecycle

from murder.runtime.agents.crow import CrowAgent
from murder.runtime.agents.runner import spawn_agent
from murder.runtime.agents.sessions import AgentScope, AgentSpec
from murder.llm.harnesses.models import HarnessStartSpec
from murder.verdict.completion import CheckRegistry, CompletionCoordinator
from murder.runtime.orchestration.ticket_ops import TicketOps
from murder.runtime.orchestration.history_ops import HistoryOps
from murder.runtime.orchestration.note_ops import NoteOps
from murder.runtime.orchestration.plan_ops import PlanOps
from murder.runtime.orchestration.agent_ops import AgentOps
from murder.runtime.orchestration.harness_config import HarnessConfigurator
from murder.runtime.orchestration.worktree_provisioner import WorktreeProvisioner
from murder.runtime.orchestration.brief_service import BriefService
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


class Orchestrator:
    """Coordination facade for spawning/killing agents and computing readiness.

    Owns COORDINATION ONLY. The heavy lifting lives in collaborators constructed
    in ``__init__`` and reached by delegation:
      • Concern ops — TicketOps / PlanOps / NoteOps / HistoryOps / AgentOps
        (the ``*_ops.py`` modules): entity mutations, messaging, lifecycle.
      • Heavy-path collaborators — HarnessConfigurator (``harness_config.py``),
        WorktreeProvisioner (``worktree_provisioner.py``), BriefService
        (``brief_service.py``): the worktree / brief / harness resolution that
        used to be inlined and duplicated across the spawn methods.

    Extending — DO NOT inline a new heavy path (DB mutation, worktree/brief/
    harness resolution, multi-step lifecycle) into a spawn or coordination
    method. That is precisely how this class grew to a 1608-line god. Instead
    put the logic in the matching Ops/collaborator — or add a new one the same
    way: take ``rt``, construct it in ``__init__``, delegate to it — and keep the
    method here a thin call sequence. See ``spawn_crow``: it now reads as
    resolve-harness → provision-worktree → build-brief → spawn, each one line.
    Ousterhout: deep collaborators behind a coordinating facade; the facade's job
    is sequencing, not implementation.
    """

    def __init__(self, rt: OrchestratorHost) -> None:
        self.rt = rt
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
        self.history = HistoryOps(rt)
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
        self.harness_cfg = HarnessConfigurator(rt)
        self.worktrees = WorktreeProvisioner(rt)
        self.briefs = BriefService(rt)

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

    async def _fail_ticket(self, ticket_id: str, reason: str) -> None:
        await self._outcomes().fail_ticket(ticket_id, reason)

    async def spawn_crow(self, ticket_id: str) -> str:
        row = _db_get_ticket(self.rt.db, ticket_id)
        if row is None:
            raise KeyError(ticket_id)
        ch = self.harness_cfg.resolve_crow(row)
        wt = await self.worktrees.for_crow(row, ch.kind)
        brief = self.briefs.build(role=AgentRole.CROW, harness_name=ch.kind, ticket=dict(row))
        spec = AgentSpec(
            role=AgentRole.CROW,
            scope=AgentScope(ticket_id=ticket_id, worktree_path=wt.worktree_path),
            harness=ch.kind,
            model=ch.startup_model,
            effort=ch.startup_effort,
            startup_prompt=brief,
            additional_workspace_dirs=wt.additional_workspace_dirs,
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
        # Double-claim guard: reattach (recovery task) and kickoff_ready both run
        # at boot. If kickoff has already claimed this ticket — a live crow handler
        # is registered, or the ticket has already moved out of in_progress — a
        # second reattach would bind a duplicate CrowAgent against the same pane.
        # Bail idempotently; the existing handler owns DONE.
        from murder.state.persistence.tickets import get_ticket_status as _get_ticket_status

        if self.rt.get_crow_handler(ticket_id) is not None:
            LOGGER.info(
                "reattach_crow: handler for %s already live — skipping reattach", ticket_id
            )
            return
        if _get_ticket_status(self.rt.db, ticket_id) != TicketStatus.IN_PROGRESS.value:
            LOGGER.info(
                "reattach_crow: ticket %s no longer in_progress — skipping reattach", ticket_id
            )
            return
        ch = self.harness_cfg.resolve_crow(row)
        harness = self.harness_cfg.adapter(ch)

        wt = await self.worktrees.for_reattach(row)

        agent = CrowAgent(
            agent_id=f"crow-{ticket_id}",
            ticket_id=ticket_id,
            session=crow_session,
            harness=harness,
            repo_root=wt.repo_root,
            startup_model=ch.startup_model,
            startup_effort=ch.startup_effort,
            worktree_path=wt.worktree_path,
            runtime=self.rt,
        )
        self.rt.register_agent(agent)
        agent.status = AgentStatus.RUNNING
        self.rt.sync_agent(agent)
        # Fresh producer state; reattach resumes transcript projection from the
        # current pane scrollback rather than the original startup state.
        agent.start_conversation()
        await self.spawn_crow_handler(ticket_id, crow_session)

    async def spawn_crow_handler(self, ticket_id: str, crow_session: str) -> str:
        row = _db_get_ticket(self.rt.db, ticket_id)
        if row is None:
            raise KeyError(ticket_id)
        ch = self.harness_cfg.resolve_crow(row)
        harness = self.harness_cfg.adapter(ch)
        session = format_session_name(self.rt, "crow_handler", f"_{ticket_id}")
        client, crow_handler_cfg = resolve_direct_role_client(
            self.rt.config.crow_handler,
            self.rt.user_cfg,
            "crow_classification",
            "crow_handler",
        )
        crow_agent = self.rt.get_crow(ticket_id)
        worktree_path = getattr(crow_agent, "worktree_path", None) if crow_agent else None
        handler = CrowHandler(
            agent_id=f"crow_handler-{ticket_id}",
            ticket_id=ticket_id,
            session=session,
            crow_session=crow_session,
            harness=harness,
            config=crow_handler_cfg,
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
        resume_session_id: str | None = None,
    ) -> str:
        """Start a ticketless crow session; inject model selection when supported.

        ``resume_session_id`` (CC-only) resumes a prior harness session in place
        (``claude --resume <id>``) instead of starting a fresh conversation; it
        is threaded onto the start spec and ignored by adapters that don't honor
        it.
        """
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

        wt = await self.worktrees.for_rogue(worktree_branch, worktree_path)
        cwd = wt.cwd
        resolved_worktree = wt.resolved_worktree

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
            resume_session_id=resume_session_id,
        )
        try:
            start_result = await agent.harness_session.start(start_spec)
            if not start_result.ok:
                message = start_result.message or "harness startup failed"
                raise RuntimeError(message)
            # Rogues bypass CrowAgent.start(), so they must bind the same
            # verified observer/controller/actuator explicitly after tmux
            # startup.  The old first-send gate was a procedural workaround;
            # verified prompt delivery waits for current composer evidence.
            await agent.initialize_verified_harness_control()
            model_result = await agent.select_verified_model(startup_model, startup_effort)
            if not model_result.ok:
                raise RuntimeError(model_result.message or "verified rogue model selection failed")
            agent.status = AgentStatus.RUNNING
            self.rt.sync_agent(agent)
            # Rogues bypass CrowAgent.start(), so kick off transcript projection
            # here: a fresh session gets fresh producer-owned parser state.
            agent.start_conversation()
            # ``sync_agent`` above committed the roster input with this state.
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

    async def resume_conversation(self, conversation_id: str) -> dict[str, Any]:
        """Resume a completed CC conversation as a fresh rogue crow.

        Reads the conversation's harness/session id from the store, validates it
        is a resumable Claude Code session, and (unless a crow is already live
        for it) spawns a rogue CC crow launched with ``claude --resume <id>``.
        Returns an error dict (never raises) when the conversation is missing,
        not CC, or has no captured session id, so the history /resume keybind can
        surface a toast instead of crashing the worker.
        """
        cid = conversation_id.strip()
        if not cid:
            return {"ok": False, "error": "resume requires conversation_id"}
        db = self.rt.db
        if db is None:
            return {"ok": False, "error": "resume unavailable: no database"}
        row = db.execute(
            """
            SELECT harness, harness_session_id, status
              FROM conversations
             WHERE conversation_id = ?
            """,
            (cid,),
        ).fetchone()
        if row is None:
            return {"ok": False, "error": f"no conversation {cid}"}
        harness = row["harness"]
        session_id = row["harness_session_id"]
        if harness != "claude_code" or not session_id:
            reason = (
                "resume is only supported for Claude Code sessions"
                if harness != "claude_code"
                else "conversation has no resumable session id"
            )
            return {"ok": False, "error": reason}
        # A live crow for this conversation means resume would fork a second copy
        # of the same session; bail with a friendly message instead.
        existing = self.rt.get_agent(cid)
        if existing is not None and await self._agent_is_live(existing):
            return {"ok": False, "error": "a session is already running for this conversation"}
        agent_id = await self.spawn_rogue(
            "claude_code",
            "",
            name=f"resume_{cid}",
            resume_session_id=str(session_id),
        )
        return {"handled": True, "agent_id": agent_id, "resumed_from": cid}

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

    async def ensure_planning_agent(
        self,
        plan_name: str,
        *,
        harness: str | None = None,
        model: str | None = None,
        effort: str | None = None,
    ) -> str:
        """Return the agent_id of a live planning agent for plan_name,
        spawning the agent + its handler if needed."""
        assert self.rt.db is not None
        agent_id = f"planner-{plan_name}"
        # setdefault keeps the get-or-create atomic; never insert an await
        # between resolving the lock and acquiring it, or two coroutines could
        # each create a distinct Lock and defeat the mutual exclusion.
        lock = self._planner_spawn_locks.setdefault(plan_name, asyncio.Lock())
        async with lock:
            agent = self.rt.get_agent(agent_id)
            if agent is not None and await self._agent_is_live(agent):
                handler = self.rt.get_agent(f"planning_handler-{plan_name}")
                if not isinstance(handler, PlanningHandler):
                    await self.spawn_planning_handler(plan_name, agent.session)
                return agent_id
            cfg = self.rt.config.planner
            resolved_harness = (harness or cfg.harness).strip()
            resolved_model = cfg.startup_model if model is None else (model.strip() or None)
            resolved_effort = cfg.startup_effort if effort is None else (
                effort.strip() if isinstance(effort, str) and effort.strip() else None
            )
            startup_prompt = self.briefs.build(
                role=AgentRole.PLANNER,
                harness_name=resolved_harness,
                plan_name=plan_name,
            )
            spec = AgentSpec(
                role=AgentRole.PLANNER,
                scope=AgentScope(plan_name=plan_name),
                harness=resolved_harness,
                model=resolved_model,
                effort=resolved_effort,
                startup_prompt=startup_prompt,
            )
            handle = await spawn_agent(spec, rt=self.rt, event_sink=self.rt.event_sink)
            # TODO: resumability — if a prior planner session exists with prior
            # transcript, future work will summarize via compact-style summary
            # and seed the new session. For now we always spawn fresh.
            # Readiness gate: don't hand the handler a session that hasn't
            # materialized yet. The handler's first capture_pane against a
            # not-yet-live session is exactly the boot pane-lag that escalates;
            # wait briefly for the tmux session to exist first. The handler also
            # carries its own startup grace, so this is best-effort, not a hard
            # barrier — we proceed after the timeout regardless.
            await self._await_session_ready(handle.session_name)
            await self.spawn_planning_handler(plan_name, handle.session_name)
            return agent_id

    async def spawn_planner(
        self,
        plan_name: str,
        harness: str,
        model: str = "",
        effort: str | None = None,
    ) -> str:
        """Start (or return) the per-plan planning agent for ``plan_name``.

        ``harness`` is required at the command boundary (the UI supplies the
        effective planner harness from settings). ``model``/``effort`` are
        optional overrides; empty model falls through to role config / adapter
        defaults.
        """
        harness_kind = harness.strip()
        if not harness_kind:
            raise ValueError("spawn_planner requires harness")
        plan = plan_name.strip()
        if not plan:
            raise ValueError("spawn_planner requires plan_name")
        model_override = model.strip() if isinstance(model, str) else ""
        return await self.ensure_planning_agent(
            plan,
            harness=harness_kind,
            model=model_override,
            effort=effort,
        )

    async def spawn_planner_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        plan_name = payload.get("plan_name")
        harness = payload.get("harness")
        model = payload.get("model")
        effort = payload.get("effort")
        if not isinstance(plan_name, str) or not plan_name.strip():
            raise ValueError("planner.spawn requires plan_name")
        if not isinstance(harness, str) or not harness.strip():
            raise ValueError("planner.spawn requires harness")
        if model is not None and not isinstance(model, str):
            raise ValueError("planner.spawn model must be a string")
        if effort is not None and not isinstance(effort, str):
            raise ValueError("planner.spawn effort must be a string")
        agent_id = await self.spawn_planner(
            plan_name.strip(),
            harness.strip(),
            model if isinstance(model, str) else "",
            effort,
        )
        return {"handled": True, "agent_id": agent_id}

    async def _await_session_ready(
        self, session_name: str, *, timeout_s: float = 5.0, interval_s: float = 0.25
    ) -> bool:
        """Poll until the planner's tmux session exists, or timeout. Best-effort.

        Returns True if the session became live within the window. Never raises —
        a missing session just means the handler's own startup grace absorbs the
        remaining lag.
        """
        from murder.runtime.terminal import tmux

        deadline = asyncio.get_running_loop().time() + max(0.0, timeout_s)
        while True:
            try:
                if await tmux.session_exists(session_name):
                    return True
            except Exception:
                return False
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(interval_s)

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
    async def ensure_startup_rogue(self) -> str | None:
        """Ensure the user's configured Startup Rogue exists (idempotent).

        Reads the user-scope ``tui.startup_rogue`` preference and, when set,
        spawns a single ticketless rogue under a *deterministic* agent id
        (``<prefix>-rogue-startup``) so repeated daemon boots reuse the live one
        rather than piling up. Returns the agent id, or ``None`` when no startup
        rogue is configured.
        """
        from murder.user_config import load_user_config

        sr = load_user_config().tui.startup_rogue
        if sr is None:
            return None
        harness_kind = (sr.harness or "claude_code").strip()
        agent_id = f"{_harness_prefix(harness_kind)}-rogue-startup"
        agent = self.rt.get_agent(agent_id)
        if agent is not None:
            if await agent.is_live():
                return agent_id
            await self.rt.reap(agent_id)
        else:
            # Persisted in the DB by a prior daemon but absent from this process's
            # registry (service restart): kill any orphaned tmux session so the
            # upcoming create_session doesn't raise "already exists", then mark dead.
            row = self.rt.db.execute(
                "SELECT session FROM agents WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            if row and row["session"] and await tmux.session_exists(row["session"]):
                with contextlib.suppress(Exception):
                    await tmux.kill_session(row["session"])
                _db_set_agent_status(self.rt.db, agent_id, "dead")
        return await self.spawn_rogue(
            harness_kind, sr.model or "", sr.effort, name="startup"
        )

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
        body = self.briefs.build(role=AgentRole.COLLABORATOR, harness_name=collab_cfg.harness)
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

    async def dismiss_history_item(self, item_id: str) -> dict[str, Any]:
        return await self.history.dismiss(item_id)

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
