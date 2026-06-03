"""Orchestration: spawn/kill agents; wave kickoff; ready computation."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

_TNUM_RE = re.compile(r"^t(\d+)$")

LOGGER = logging.getLogger(__name__)

from murder import notes as notes_mod
from murder.persistence.tickets import (
    get_ticket as _db_get_ticket,
    compute_ready as _db_compute_ready,
    list_tickets_in_wave as _db_list_tickets_in_wave,
    update_ticket_status as _db_update_ticket_status,
    apply_ticket_carve_payload as _db_apply_ticket_carve_payload,
)
from murder.persistence.plans import (
    get_plan_row as _db_get_plan_row,
    upsert_plan as _db_upsert_plan,
)
from murder.persistence.agents import (
    upsert_agent as _db_upsert_agent,
    get_active_agent_by_role as _db_get_active_agent_by_role,
    set_agent_status as _db_set_agent_status,
    rename_agent as _db_rename_agent,
)
from murder.agents.base import AgentRole, AgentStatus
from murder.agents.crow_handler import CrowHandler
from murder.agents.planning_handler import PlanningHandler
from murder.bus import StatusChangeEvent, TicketStatus
from murder.clients import resolve_role_client
from murder.config import (
    resolve_default_crow_harness,
    resolve_default_crow_startup_effort,
    resolve_default_crow_startup_model,
)
from murder.harnesses import get as get_harness
from murder.plans.parser import (
    render as _render_plan_markdown,
    write as _write_plan_markdown,
)
from murder.plans.schema import Plan, PlanStatus
from murder.plans.sync import content_hash as _plan_content_hash
from murder.storage.paths import plan_md, ticket_md, tickets_dir
from murder.storage.worktrees import (
    ensure_crow_worktree,
    ensure_named_worktree,
    prune_terminal_crow_worktree,
)
from murder.terminal import tmux
from murder.terminal.session_names import format_session_name
from murder.tickets import carve, lifecycle

from murder.agents.crow import CrowAgent
from murder.agents.runner import spawn_agent
from murder.agents.sessions import AgentScope, AgentSpec
from murder.harnesses.models import HarnessStartSpec
from murder.completion import CheckRegistry, CompletionCoordinator
from murder.harnesses import capabilities_for
from murder.orchestration.brief import BriefContext, assembler_for
from murder.service.runtime_scope import OrchestratorHost

from ..escalations.service import EscalationService
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


def is_rogue_agent_id(agent_id: str) -> bool:
    """True for any rogue agent id regardless of harness prefix."""
    return "rogue-" in agent_id


def _crow_handler_companion(agent_id: str) -> str:
    """The crow_handler id paired with a ``crow-<ticket>`` agent, else itself.

    Used to tear down both halves of a ticket crow when force-stopping an
    agent the runtime no longer tracks. Returns ``agent_id`` unchanged when
    there is no separate handler (e.g. rogue crows), so callers can pass it
    to a query without a special case.
    """
    if agent_id.startswith("crow-"):
        return f"crow_handler-{agent_id[len('crow-'):]}"
    return agent_id


def _validate_plan_filename_stem(name: str, *, command: str) -> str:
    name = name.strip()
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        raise ValueError(f"{command} name must be a single filename stem")
    return name


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
            from murder.persistence.tickets import get_ticket_status

            if get_ticket_status(conn, tid) != TicketStatus.IN_PROGRESS.value:
                await self._fail_ticket(
                    tid,
                    f"kickoff status drift: expected in_progress, got {get_ticket_status(conn, tid)}",
                )
                continue
            kicked.append(tid)
        return kicked

    async def quick_kick_ticket(self, title: str) -> dict[str, Any]:
        """Create a ticket, insert it into the DB as PLANNED, and immediately kick it."""
        assert self.rt.db is not None
        conn = self.rt.db
        repo_root = self.rt.repo_root

        # Derive next ID from DB + file system to avoid races with TicketSync.
        max_n = 0
        for row in conn.execute("SELECT id FROM tickets WHERE id LIKE 't%'").fetchall():
            m = _TNUM_RE.match(str(row["id"]))
            if m:
                max_n = max(max_n, int(m.group(1)))
        root = tickets_dir(repo_root)
        if root.exists():
            for p in root.glob("*.md"):
                m2 = _TNUM_RE.match(p.stem)
                if m2:
                    max_n = max(max_n, int(m2.group(1)))
        ticket_id = f"t{max_n + 1:03d}"

        # Write the markdown file so the sidecar sync stays consistent.
        path = ticket_md(repo_root, ticket_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {title}\n\n## Plan\n\n## Working Notes\n")

        # Insert directly into DB — bypasses the 1.5 s TicketSync poll.
        from murder.persistence.tickets import insert_ticket as _db_insert_ticket
        from murder.tickets.schema import Ticket
        from murder.tickets.status import TicketStatus

        now = datetime.utcnow().replace(microsecond=0)
        row_existing = conn.execute(
            "SELECT id FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        if row_existing is None:
            ticket = Ticket(
                id=ticket_id,
                title=title,
                wave=1,
                status=TicketStatus.PLANNED,
                created_at=now,
                updated_at=now,
            )
            try:
                _db_insert_ticket(conn, ticket)
            except Exception:
                pass  # TicketSync may have raced us

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
        harness_kind = resolve_default_crow_harness(self.rt.config.default_crow, row)
        startup_model = resolve_default_crow_startup_model(
            self.rt.config.default_crow, row, harness_kind
        )
        startup_effort = resolve_default_crow_startup_effort(self.rt.config.default_crow, row)
        worktree_path: str | None = None
        if self.rt.config.runtime.use_worktrees:
            worktree = await ensure_crow_worktree(self.rt.repo_root, ticket_id)
            worktree_path = str(worktree.path)
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
        )
        handle = await spawn_agent(spec, rt=self.rt, event_sink=self.rt.event_sink)
        return handle.session_name

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
                known_startup_models = {
                    model_id for model_id, _label in harness_adapter.available_startup_models
                }
                codex_startup_model_gate = (
                    harness_kind == "codex"
                    and startup_model in known_startup_models
                    and "failed to select runtime model" in message
                )
                if not codex_startup_model_gate:
                    raise RuntimeError(message)
            agent.status = AgentStatus.RUNNING
            self.rt.sync_agent(agent)
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

    async def send_agent_message(
        self, agent_id: str, message: str, ticket_id: str | None
    ) -> dict[str, Any]:
        """Deliver a message to an agent by id.

        Planner targets are restored on demand so a selected plan can receive
        chat even if its tmux session has not been started yet.
        """
        del ticket_id

        agent = self.rt.get_agent(agent_id)
        if agent_id.startswith("planner-"):
            plan_name = agent_id[len("planner-") :]
            if not plan_name:
                return {"handled": False, "error": "planner agent_id requires a plan name"}
            if agent is None or not await self._agent_is_live(agent):
                await self.ensure_planning_agent(plan_name)
                agent = self.rt.get_agent(agent_id)
        if agent_id.startswith("crow-"):
            ticket_id = agent_id[len("crow-") :]
            if not ticket_id:
                return {"handled": False, "error": "crow agent_id requires a ticket id"}
            handler = self.rt.get_crow_handler(ticket_id)
            if handler is not None:
                queue_result = await handler.queue_message(message)
                return {"handled": True, **queue_result}
        if agent is None:
            return {"handled": False, "error": f"no agent named {agent_id}"}
        await agent.send(message)
        return {"handled": True, "queued": False}

    async def send_agent_key(
        self, agent_id: str | None, key: str, *, literal: bool = False
    ) -> dict[str, Any]:
        """Send a raw tmux key (name or literal text) to an agent harness pane."""
        if agent_id is None:
            agent_id = await self.ensure_collaborator()

        agent = self.rt.get_agent(agent_id)
        if agent_id.startswith("planner-"):
            plan_name = agent_id[len("planner-") :]
            if not plan_name:
                return {"handled": False, "error": "planner agent_id requires a plan name"}
            if agent is None or not await self._agent_is_live(agent):
                await self.ensure_planning_agent(plan_name)
                agent = self.rt.get_agent(agent_id)
        if agent is None:
            return {"handled": False, "error": f"no agent named {agent_id}"}

        session = getattr(agent, "session", None)
        if not isinstance(session, str) or not session:
            return {"handled": False, "error": f"agent {agent_id} has no tmux session"}

        await tmux.send_keys(session, key, literal=literal, enter=False)
        return {
            "handled": True,
            "agent_id": agent_id,
            "session": session,
            "key": key,
            "literal": literal,
        }

    async def stop_agent(self, agent_id: str) -> dict[str, Any]:
        """Stop a live agent and tear down its tmux session."""
        if self.rt.get_agent(agent_id) is None:
            # Not in the in-memory registry. The roster derives "running" from
            # the agents table, so a crow spawned in a prior service run shows
            # up as killable even though its handle was never re-registered
            # (its tmux session may well still be live). Tear it down directly
            # so murda works after a service restart instead of bailing with
            # "no agent named X".
            return await self._force_stop_unregistered_agent(agent_id)
        if agent_id.startswith("crow-"):
            ticket_id = agent_id[len("crow-") :]
            if ticket_id:
                await self._reap_ticket_crow_agents(ticket_id)
                return {"handled": True, "agent_id": agent_id}
        await self.rt.reap(agent_id)
        return {"handled": True, "agent_id": agent_id}

    async def _force_stop_unregistered_agent(self, agent_id: str) -> dict[str, Any]:
        """Kill the tmux session and mark dead an agent the runtime forgot."""
        db = self.rt.db
        if db is None:
            return {"handled": False, "error": f"no agent named {agent_id}"}
        rows = db.execute(
            """
            SELECT agent_id, session FROM agents
             WHERE (agent_id = ? OR agent_id = ?)
               AND status NOT IN ('done', 'dead')
            """,
            (agent_id, _crow_handler_companion(agent_id)),
        ).fetchall()
        if not rows:
            return {"handled": False, "error": f"no agent named {agent_id}"}
        for row in rows:
            session = row["session"]
            if session and await tmux.session_exists(session):
                with contextlib.suppress(tmux.TmuxError):
                    await tmux.kill_session(session)
            _db_set_agent_status(db, row["agent_id"], AgentStatus.DEAD.value)
        return {"handled": True, "agent_id": agent_id}

    async def rename_rogue_agent(self, agent_id: str, name: str) -> dict[str, Any]:
        """Rename a live rogue crow without restarting its harness."""
        if not is_rogue_agent_id(agent_id):
            return {"handled": False, "error": "rename is only supported for rogue crows"}
        agent = self.rt.get_agent(agent_id)
        if agent is None:
            return {"handled": False, "error": f"no agent named {agent_id}"}
        match = re.match(r"^(.+)-rogue-(.+)$", agent_id)
        if match is None:
            return {"handled": False, "error": f"cannot parse rogue agent id {agent_id}"}
        prefix = match.group(1)
        slug = _rogue_slug(name)
        new_agent_id = f"{prefix}-rogue-{slug}"
        if new_agent_id == agent_id:
            return {"handled": True, "agent_id": agent_id}
        if self.rt.get_agent(new_agent_id) is not None:
            return {"handled": False, "error": f"agent already exists: {new_agent_id}"}

        old_session = getattr(agent, "session", None)
        new_session = format_session_name(self.rt, "crow", f"_{prefix}_rogue_{slug}")
        if (
            isinstance(old_session, str)
            and old_session != new_session
            and await tmux.session_exists(new_session)
        ):
            return {"handled": False, "error": f"session already exists: {new_session}"}

        renamed = self.rt.agents.rename_agent(
            agent_id,
            new_agent_id,
            persist=self.rt.sync_agent,
        )
        if renamed is None:
            return {"handled": False, "error": f"failed to rename {agent_id}"}
        if isinstance(old_session, str) and old_session != new_session:
            if await tmux.session_exists(old_session):
                await tmux.rename_session(old_session, new_session)
            renamed.session = new_session
            harness_session = getattr(renamed, "harness_session", None)
            if harness_session is not None:
                harness_session.session = new_session
        if self.rt.db is not None:
            with self.rt.db:
                _db_rename_agent(
                    self.rt.db,
                    agent_id,
                    new_agent_id,
                    session=getattr(renamed, "session", None),
                )
            self.rt.sync_agent(renamed)
        return {
            "handled": True,
            "old_agent_id": agent_id,
            "agent_id": new_agent_id,
        }

    async def interrupt_agent(self, agent_id: str) -> dict[str, Any]:
        if is_rogue_agent_id(agent_id):
            agent = self.rt.get_agent(agent_id)
            if agent is None:
                return {"handled": False, "error": f"no agent named {agent_id}"}
            harness_session = getattr(agent, "harness_session", None)
            if harness_session is None:
                return {"handled": False, "error": f"agent {agent_id} has no harness session"}
            await harness_session.interrupt()
            return {"handled": True}
        if not agent_id.startswith("crow-"):
            return {"handled": False, "error": "interrupt is only supported for crow agents"}
        ticket_id = agent_id[len("crow-") :]
        if not ticket_id:
            return {"handled": False, "error": "crow agent_id requires a ticket id"}
        handler = self.rt.get_crow_handler(ticket_id)
        if handler is None:
            return {"handled": False, "error": f"no crow_handler for {ticket_id}"}
        await handler.interrupt_crow()
        return {"handled": True}

    async def _agent_is_live(self, agent: Any) -> bool:
        try:
            live = bool(await agent.is_live())
        except Exception:
            return False
        if getattr(agent, "role", None) == AgentRole.PLANNER:
            session = getattr(agent, "session", None)
            if not isinstance(session, str) or not session:
                return False
            return live and await tmux.session_exists(session)
        return live

    async def scaffold_plan(self, name: str, body: str) -> dict[str, Any]:
        """Create or refresh a draft plan row and its materialized markdown."""
        assert self.rt.db is not None
        name = _validate_plan_filename_stem(name, command="plan.scaffold")
        now = datetime.utcnow()
        plan = Plan(
            name=name,
            status=PlanStatus.DRAFT,
            created_at=now,
            updated_at=now,
            related_tickets=[],
            frontmatter={},
            body=body,
        )
        path = plan_md(self.rt.repo_root, name)
        materialized_path = str(path.relative_to(self.rt.repo_root))
        rendered = _render_plan_markdown(plan)
        content_hash = _plan_content_hash(rendered)
        with self.rt.db:
            _db_upsert_plan(
                self.rt.db,
                plan,
                content_hash=content_hash,
                materialized_path=materialized_path,
                file_hash=content_hash,
                sync_state="synced",
                create_revision=True,
                revision_source="db",
            )
            _write_plan_markdown(path, plan)
        row = _db_get_plan_row(self.rt.db, name) or {}
        return {
            "handled": True,
            "name": name,
            "materialized_path": materialized_path,
            "revision_count": row.get("revision_count"),
        }

    async def rename_plan(self, old_name: str, new_name: str) -> dict[str, Any]:
        """Explicit first-class plan rename with live planner continuity."""
        assert self.rt.db is not None
        old_name = _validate_plan_filename_stem(old_name, command="plan.rename")
        new_name = _validate_plan_filename_stem(new_name, command="plan.rename")
        if old_name == new_name:
            row = _db_get_plan_row(self.rt.db, old_name)
            if row is None:
                raise KeyError(old_name)
            return {
                "handled": True,
                "old_name": old_name,
                "name": new_name,
                "materialized_path": row["materialized_path"],
                "revision_count": row.get("revision_count"),
            }
        if _db_get_plan_row(self.rt.db, old_name) is None:
            raise KeyError(old_name)
        if _db_get_plan_row(self.rt.db, new_name) is not None:
            raise ValueError(f"plan already exists: {new_name}")
        await self._preflight_plan_runtime_rename(old_name, new_name)
        if self.rt.plan_sync is None:
            raise RuntimeError("plan sync not available")
        row = self.rt.plan_sync.rename_plan(old_name, new_name)
        await self._retarget_plan_runtime(old_name, new_name)
        return {
            "handled": True,
            "old_name": old_name,
            "name": new_name,
            "materialized_path": row.get("materialized_path"),
            "revision_count": row.get("revision_count"),
        }

    async def deprecate_plan(self, name: str) -> dict[str, Any]:
        """Mark a plan superseded and remove it from active planning."""
        assert self.rt.db is not None
        name = _validate_plan_filename_stem(name, command="plan.deprecate")
        if self.rt.plan_sync is None:
            raise RuntimeError("plan sync not available")
        row = self.rt.plan_sync.deprecate_plan(name)
        for agent_id in (f"planning_handler-{name}", f"planner-{name}"):
            if self.rt.get_agent(agent_id) is not None:
                await self.rt.reap(agent_id)
            else:
                _db_set_agent_status(self.rt.db, agent_id, AgentStatus.DEAD.value)
        return {
            "handled": True,
            "name": name,
            "status": row.get("status"),
            "materialized_path": row.get("materialized_path"),
            "revision_count": row.get("revision_count"),
        }

    async def _preflight_plan_runtime_rename(self, old_name: str, new_name: str) -> None:
        planner = self.rt.get_agent(f"planner-{old_name}")
        if planner is not None:
            old_session = format_session_name(self.rt, "planner", f"_{old_name}")
            new_session = format_session_name(self.rt, "planner", f"_{new_name}")
            if await tmux.session_exists(old_session) and await tmux.session_exists(
                new_session
            ):
                raise tmux.TmuxError(f"session already exists: {new_session}")
        handler = self.rt.get_agent(f"planning_handler-{old_name}")
        if handler is not None:
            old_session = format_session_name(self.rt, "planning_handler", f"_{old_name}")
            new_session = format_session_name(self.rt, "planning_handler", f"_{new_name}")
            if await tmux.session_exists(old_session) and await tmux.session_exists(
                new_session
            ):
                raise tmux.TmuxError(f"session already exists: {new_session}")

    async def _retarget_plan_runtime(self, old_name: str, new_name: str) -> None:
        assert self.rt.db is not None
        old_lock = self._planner_spawn_locks.pop(old_name, None)
        if old_lock is not None:
            self._planner_spawn_locks[new_name] = old_lock

        old_planner_id = f"planner-{old_name}"
        new_planner_id = f"planner-{new_name}"
        old_planner_session = format_session_name(self.rt, "planner", f"_{old_name}")
        new_planner_session = format_session_name(self.rt, "planner", f"_{new_name}")
        planner = self.rt.agents.rename_agent(old_planner_id, new_planner_id)
        await tmux.rename_session(old_planner_session, new_planner_session)
        if planner is not None:
            planner.session = new_planner_session
            if hasattr(planner, "plan_name"):
                planner.plan_name = new_name
            harness_session = getattr(planner, "harness_session", None)
            if harness_session is not None:
                harness_session.session = new_planner_session

        old_handler_id = f"planning_handler-{old_name}"
        new_handler_id = f"planning_handler-{new_name}"
        old_handler_session = format_session_name(self.rt, "planning_handler", f"_{old_name}")
        new_handler_session = format_session_name(self.rt, "planning_handler", f"_{new_name}")
        handler = self.rt.agents.rename_agent(old_handler_id, new_handler_id)
        await tmux.rename_session(old_handler_session, new_handler_session)
        if handler is not None:
            handler.session = new_handler_session
            if hasattr(handler, "plan_name"):
                handler.plan_name = new_name
            if hasattr(handler, "planner_session"):
                handler.planner_session = new_planner_session

        with self.rt.db:
            _db_rename_agent(
                self.rt.db,
                old_planner_id,
                new_planner_id,
                session=new_planner_session,
            )
            _db_rename_agent(
                self.rt.db,
                old_handler_id,
                new_handler_id,
                session=new_handler_session,
            )
            if planner is not None:
                self.rt.sync_agent(planner)
            if handler is not None:
                self.rt.sync_agent(handler)

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

    async def submit_notetaker_capture(self, payload: dict[str, Any]) -> dict[str, Any]:
        assert self.rt.db is not None

        raw = payload.get("raw")
        if raw is None:
            raw = payload.get("text")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(
                "notetaker.capture.submit requires non-empty payload.raw or payload.text"
            )

        client = resolve_role_client(self.rt.config.notetaker)
        return await notes_mod.submit_capture(
            repo_root=self.rt.repo_root,
            conn=self.rt.db,
            raw=raw.strip(),
            client=client,
            config=self.rt.config.notetaker,
            note_name=notes_mod.today_name(),
        )

    async def ensure_note(self, name: str) -> dict[str, Any]:
        assert self.rt.db is not None
        row = notes_mod.ensure_note(self.rt.db, self.rt.repo_root, name)
        return {"name": name, "materialized_path": str(row.get("materialized_path", ""))}

    async def retire_note(self, name: str) -> dict[str, Any]:
        assert self.rt.db is not None
        try:
            dest = notes_mod.retire_note(self.rt.db, self.rt.repo_root, name)
        except Exception as exc:
            raise ValueError(f"could not retire note: {exc}") from exc
        return {"name": name, "dest_name": dest.name}

    async def evaluate_wave_completion(self, wave: int) -> bool:
        assert self.rt.db is not None
        tickets = _db_list_tickets_in_wave(self.rt.db, wave)
        if not tickets:
            return True
        return all(t["status"] == TicketStatus.DONE.value for t in tickets)

    async def reopen_ticket(self, ticket_id: str) -> list[str]:
        assert self.rt.db is not None
        cascaded = lifecycle.reopen(self.rt.db, ticket_id)
        for tid in {ticket_id, *cascaded}:
            await self._reap_ticket_crow_agents(tid)
        return list(cascaded)

    async def retry_failed_ticket(self, ticket_id: str) -> dict[str, Any]:
        """Transition a failed ticket back to ready and clear its last_error."""
        assert self.rt.db is not None
        prev = lifecycle.transition(self.rt.db, ticket_id, TicketStatus.READY, reason="retry")
        lifecycle.clear_last_error(self.rt.db, ticket_id)
        await self._reap_ticket_crow_agents(ticket_id)
        await self._emit_ticket_status(ticket_id, prev, TicketStatus.READY.value)
        return {"handled": True, "ticket_id": ticket_id, "prev_status": prev.value}

    async def set_schedule_at(self, ticket_id: str, schedule_at: str | None) -> dict[str, Any]:
        """Update the schedule_at timestamp for a ticket."""
        assert self.rt.db is not None
        now = datetime.now().isoformat(timespec="seconds")
        self.rt.db.execute(
            "UPDATE tickets SET schedule_at = ?, updated_at = ? WHERE id = ?",
            (schedule_at, now, ticket_id),
        )
        self.rt.db.commit()
        return {"handled": True, "ticket_id": ticket_id, "schedule_at": schedule_at}

    async def update_ticket_metadata(
        self, ticket_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Update metadata fields directly without state-machine transitions."""
        assert self.rt.db is not None
        row = _db_get_ticket(self.rt.db, ticket_id)
        if row is None:
            return {"handled": True, "ok": False, "error": f"ticket not found: {ticket_id}"}
        title = str(payload.get("title") or row.get("title") or "").strip()
        if not title:
            return {"handled": True, "ok": False, "error": "title is required"}
        wave_raw = payload.get("wave")
        try:
            wave = int(wave_raw) if wave_raw is not None else int(row.get("wave", 0))
        except (TypeError, ValueError):
            return {"handled": True, "ok": False, "error": "wave must be an integer"}
        harness = str(payload.get("harness") or row.get("harness") or "cursor").strip()
        model = payload.get("model") or None
        if model is not None:
            model = str(model).strip() or None
        schedule_at = payload.get("schedule_at")
        if schedule_at is not None:
            schedule_at = str(schedule_at).strip() or None
        deps = [str(d) for d in (payload.get("deps") or [])]
        if "skills" in payload:
            skills = [str(s) for s in (payload.get("skills") or [])]
        else:
            skills = [str(s) for s in (row.get("skills") or [])]
        checklist = [str(c) for c in (payload.get("checklist") or [])]
        with self.rt.db:
            self.rt.db.execute(
                "UPDATE tickets SET wave=?, schedule_at=? WHERE id=?",
                (wave, schedule_at, ticket_id),
            )
            _db_apply_ticket_carve_payload(
                self.rt.db,
                ticket_id,
                title=title,
                harness=harness,
                model=model,
                deps=deps,
                skills=skills,
                checklist=checklist,
            )
        return {"handled": True, "ok": True, "ticket_id": ticket_id}

    async def force_ticket_status(self, ticket_id: str, status: str) -> dict[str, Any]:
        """Force-set ticket status regardless of current state."""
        assert self.rt.db is not None
        valid = {"planned", "ready", "in_progress", "blocked", "failed", "done", "archived"}
        if status not in valid:
            return {"handled": True, "ok": False, "error": f"invalid status: {status!r}"}
        row = _db_get_ticket(self.rt.db, ticket_id)
        if row is None:
            return {"handled": True, "ok": False, "error": f"ticket not found: {ticket_id}"}
        prev_str = str(row.get("status") or "planned")
        with self.rt.db:
            _db_update_ticket_status(self.rt.db, ticket_id, status)
            if prev_str == "failed" and status != "failed":
                lifecycle.clear_last_error(self.rt.db, ticket_id)
        try:
            prev = TicketStatus(prev_str)
        except ValueError:
            prev = TicketStatus.PLANNED
        await self._emit_ticket_status(ticket_id, prev, status)
        if status in (
            TicketStatus.DONE.value,
            TicketStatus.FAILED.value,
            TicketStatus.ARCHIVED.value,
        ):
            await self._reap_ticket_crow_agents(ticket_id)
            if self.rt.db is not None:
                with contextlib.suppress(Exception):
                    await prune_terminal_crow_worktree(
                        self.rt.db, self.rt.repo_root, ticket_id
                    )
        return {"handled": True, "ok": True, "ticket_id": ticket_id, "prev_status": prev_str}

    async def _reap_ticket_crow_agents(self, ticket_id: str) -> None:
        await self.rt.reap(f"crow-{ticket_id}")
        await self.rt.reap(f"crow_handler-{ticket_id}")

    async def apply_ticket_carve_ready(
        self, ticket_id: str, payload: dict[str, object]
    ) -> dict[str, object]:
        """Apply carved sidecar from structured ``carve`` or legacy ``yaml`` string."""
        assert self.rt.db is not None
        carve_body = payload.get("carve")
        yaml_text = payload.get("yaml")
        try:
            if isinstance(carve_body, dict) and carve_body:
                spec = dict(carve_body)
                if spec.get("id") is None:
                    spec["id"] = ticket_id
                prev = carve.ingest_carve_ready_spec(
                    conn=self.rt.db,
                    repo_root=str(self.rt.repo_root),
                    ticket_id=ticket_id,
                    spec=spec,
                )
            elif isinstance(yaml_text, str) and yaml_text.strip():
                spec = carve.parse_carve_yaml(yaml_text)
                prev = carve.ingest_carve_ready_spec(
                    conn=self.rt.db,
                    repo_root=str(self.rt.repo_root),
                    ticket_id=ticket_id,
                    spec=spec,
                )
            else:
                return {
                    "handled": True,
                    "ok": False,
                    "error": "payload must include non-empty 'carve' object or 'yaml' string",
                }
        except carve.CarveError as exc:
            return {"handled": True, "ok": False, "error": str(exc)}
        await self._emit_ticket_status(ticket_id, prev, TicketStatus.READY.value)
        return {"handled": True, "ok": True, "ticket_id": ticket_id}
