"""Agent messaging/control concern extracted from the Orchestrator (move-code)."""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from murder.app.service.runtime_scope import OrchestratorHost
from murder.bus import Entity
from murder.runtime.agents.base import AgentRole, AgentStatus
from murder.runtime.orchestration.agent_ids import is_rogue_agent_id
from murder.runtime.terminal import tmux
from murder.runtime.terminal.session_names import format_session_name
from murder.state.persistence.agents import (
    rename_agent as _db_rename_agent,
)
from murder.state.persistence.agents import (
    set_agent_status as _db_set_agent_status,
)

LOGGER = logging.getLogger(__name__)

EnsurePlanningAgent = Callable[[str], Awaitable[str]]
EnsureCollaborator = Callable[[], Awaitable[str]]
ReapTicketCrowAgents = Callable[[str], Awaitable[None]]
RogueSlug = Callable[[str | None], str]


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


class AgentOps:
    """Agent messaging, keys, transcripts, stop/interrupt/rename over a host."""

    def __init__(
        self,
        rt: OrchestratorHost,
        *,
        ensure_planning_agent: EnsurePlanningAgent,
        ensure_collaborator: EnsureCollaborator,
        reap_ticket_crow_agents: ReapTicketCrowAgents,
        rogue_slug: RogueSlug,
        agent_is_live: Callable[[Any], Awaitable[bool]] | None = None,
    ) -> None:
        self.rt = rt
        self._ensure_planning_agent = ensure_planning_agent
        self._ensure_collaborator = ensure_collaborator
        self._reap_ticket_crow_agents = reap_ticket_crow_agents
        self._rogue_slug = rogue_slug
        # Cross-call hook so a facade-level monkeypatch of ``_agent_is_live`` is
        # honored by the message/key/transcript paths. Defaults to our own impl.
        self._agent_is_live_hook = agent_is_live or self._agent_is_live

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

    async def _record_user_block(self, agent_id: str, text: str) -> None:
        """Record a ground-truth user turn at the send boundary.

        Writes directly through the runtime db keyed by ``agent_id`` (the
        conversation id), so the exact text the user sent is stored
        authoritatively instead of re-derived from a noisy pane capture — the
        source of the collaborator corruption. No-op without a db.
        """
        db = getattr(self.rt, "db", None)
        if db is None:
            return
        from murder.bus import ConversationBlockEvent
        from murder.state.persistence import conversation

        block = conversation.append_user_message(db, agent_id, text)
        bus = getattr(self.rt, "bus", None)
        run_id = getattr(self.rt, "run_id", None)
        if block is None or bus is None or run_id is None:
            return
        agent = self.rt.get_agent(agent_id)
        await bus.publish(
            ConversationBlockEvent(
                run_id=str(run_id),
                agent_id=agent_id,
                role=getattr(agent, "role", None),
                ticket_id=getattr(agent, "ticket_id", None),
                conversation_id=agent_id,
                action="block-appended",
                block=conversation.block_to_wire(block),
            )
        )
        # History view: a new user turn is a new intention in the history feed.
        # Emit a key-only history snapshot (mirroring the PLAN publish below) so
        # connected clients refetch and surface it. Read-model spine, no extra write.
        await self.rt.publish_snapshot(Entity.HISTORY, agent_id)
        # F1 (plan sort-order): the plans list orders by MAX(captured_at) of each
        # plan's ``planner-{name}`` messages (read_model.get_plans_snapshot), so a
        # user turn to a planner reorders the list WITHOUT any plans-table write.
        # This is the low-rate, plan-scoped, runtime-layer choke point for that
        # reorder; emit a key-only plan snapshot. (The high-rate poll-driven
        # ``merge_transcript`` rebuild that ALSO re-sorts is deferred per the
        # plan's coalescing caveat -- see commit message follow-up.)
        if agent_id.startswith("planner-"):
            await self.rt.publish_snapshot(Entity.PLAN, agent_id[len("planner-"):])

    async def send_agent_message(
        self,
        agent_id: str,
        message: str,
        ticket_id: str | None,
        *,
        spawn_if_needed: bool = True,
    ) -> dict[str, Any]:
        """Deliver a message to an agent by id.

        Planner targets are restored on demand so a selected plan can receive
        chat even if its tmux session has not been started yet. Set
        ``spawn_if_needed=False`` to deliver only to an already-live planner —
        a non-live planner is left dormant (no ``ensure_planning_agent``), so
        system nudges such as plan parse-error notifications never wake it.
        """
        del ticket_id

        agent = self.rt.get_agent(agent_id)
        if agent_id.startswith("planner-"):
            plan_name = agent_id[len("planner-") :]
            if not plan_name:
                return {"ok": False, "error": "planner agent_id requires a plan name"}
            if agent is None or not await self._agent_is_live_hook(agent):
                if not spawn_if_needed:
                    LOGGER.info(
                        "send_agent_message: planner %s not live and spawn_if_needed=False; "
                        "skipping spawn",
                        agent_id,
                    )
                    return {"ok": False, "error": "agent-not-live"}
                await self._ensure_planning_agent(plan_name)
                agent = self.rt.get_agent(agent_id)
        is_crow_target = agent_id.startswith("crow-") or is_rogue_agent_id(agent_id)
        if is_crow_target and agent is not None and hasattr(agent, "queue_message"):
            # Deliver-only-when-idle for every crow (ticketed AND rogue): the
            # agent-level queue checks the harness pane and holds the message
            # until the parser reports awaiting_input (HarnessBackedAgent.
            # queue_message), mirroring queued state to the DB/bus so the TUI
            # renders it. This is the sole crow delivery path — a crow with no
            # live agent handle is simply not addressable and falls through to
            # the honest "no agent named" failure below.
            queue_result = await agent.queue_message(message)
            if queue_result.get("ok") is False:
                return {
                    "ok": False,
                    "error": str(queue_result.get("error") or "crow message delivery failed"),
                    **queue_result,
                }
            # Ground truth: record the user turn once immediate or queued
            # delivery is accepted.
            await self._record_user_block(agent_id, message)
            return {"handled": True, **queue_result}
        if agent is None:
            return {"ok": False, "error": f"no agent named {agent_id}"}
        send_result = await agent.send(message)
        if send_result is not None and getattr(send_result, "ok", True) is False:
            return {
                "ok": False,
                "error": getattr(send_result, "message", None) or "agent message delivery failed",
            }
        await self._record_user_block(agent_id, message)
        return {"handled": True, "queued": False}

    async def send_agent_key(
        self,
        agent_id: str | None,
        key: str,
        *,
        literal: bool = False,
        enter: bool = False,
        log_user_input: str | None = None,
    ) -> dict[str, Any]:
        """Send a raw tmux key (name or literal text) to an agent harness pane."""
        if agent_id is None:
            agent_id = await self._ensure_collaborator()

        agent = self.rt.get_agent(agent_id)
        if agent_id.startswith("planner-"):
            plan_name = agent_id[len("planner-") :]
            if not plan_name:
                return {"ok": False, "error": "planner agent_id requires a plan name"}
            if agent is None or not await self._agent_is_live_hook(agent):
                await self._ensure_planning_agent(plan_name)
                agent = self.rt.get_agent(agent_id)
        if agent is None:
            return {"ok": False, "error": f"no agent named {agent_id}"}

        session = getattr(agent, "session", None)
        if not isinstance(session, str) or not session:
            return {"ok": False, "error": f"agent {agent_id} has no tmux session"}

        await tmux.send_keys(session, key, literal=literal, enter=enter)
        # Ground truth: record raw-key user input authoritatively in both the
        # JSON store and the flat log (always-log-user-input is non-negotiable).
        if isinstance(log_user_input, str) and log_user_input.strip():
            await self._record_user_block(agent_id, log_user_input)
        return {
            "handled": True,
            "agent_id": agent_id,
            "session": session,
            "key": key,
            "literal": literal,
            "enter": enter,
            "logged_user_input": bool(log_user_input and log_user_input.strip()),
        }

    async def refresh_agent_transcript(self, agent_id: str) -> dict[str, Any]:
        """Project an agent's pane server-side and return the rich conversation
        doc for the TUI to render (crows + planners).

        This is the server-side mirror of the collaborator's
        ``collaborator.transcript.refresh`` RPC: parsing happens here, never in
        the TUI. Planner targets are restored on demand. Returns
        ``available=False`` with an empty doc when the agent or its parser is
        absent (the TUI falls back to the raw pane mirror).
        """
        agent = self.rt.get_agent(agent_id)
        if agent_id.startswith("planner-"):
            plan_name = agent_id[len("planner-") :]
            if plan_name and (agent is None or not await self._agent_is_live_hook(agent)):
                await self._ensure_planning_agent(plan_name)
                agent = self.rt.get_agent(agent_id)
        if agent is None or not hasattr(agent, "refresh_transcript_doc"):
            return {"handled": True, "available": False, "doc": None}
        doc = await agent.refresh_transcript_doc()
        return {
            "handled": True,
            "available": True,
            "doc": doc,
            "has_parser": agent.harness.has_transcript_parser(),
            "harness_kind": str(agent.harness.kind),
            "session": str(agent.session),
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
        if agent_id.startswith("planner-"):
            # ctrl+m on a planner must also reap its planning_handler companion,
            # else the orphaned handler polls a now-dead session and escalates
            # ("planner missed in poll" red toasts). Reap the planner first so
            # the handler's own planner-gone check would also self-terminate; the
            # explicit reap here makes teardown immediate and deterministic.
            await self.rt.reap(agent_id)
            await self._reap_planner_handler(agent_id[len("planner-") :])
            return {"handled": True, "agent_id": agent_id}
        await self.rt.reap(agent_id)
        return {"handled": True, "agent_id": agent_id}

    async def _reap_planner_handler(self, plan_name: str) -> None:
        """Reap the planning_handler paired with a ``planner-<plan>`` agent.

        Idempotent: a missing/already-dead handler is a no-op. Mirrors the
        crow/crow_handler companion teardown so murdering a planner leaves no
        orphaned relay behind.
        """
        if not plan_name:
            return
        handler_id = f"planning_handler-{plan_name}"
        if self.rt.get_agent(handler_id) is not None:
            with contextlib.suppress(Exception):
                await self.rt.reap(handler_id)
            return
        # Not in the in-memory registry (e.g. a prior service run). Tear down its
        # log-tail session and mark it dead so the roster stops showing it.
        db = self.rt.db
        if db is None:
            return
        row = db.execute(
            "SELECT agent_id, session FROM agents "
            "WHERE agent_id = ? AND status NOT IN ('done', 'dead')",
            (handler_id,),
        ).fetchone()
        if row is None:
            return
        session = row["session"]
        if session and await tmux.session_exists(session):
            with contextlib.suppress(tmux.TmuxError):
                await tmux.kill_session(session)
        _db_set_agent_status(db, handler_id, AgentStatus.DEAD.value)

    async def _force_stop_unregistered_agent(self, agent_id: str) -> dict[str, Any]:
        """Kill the tmux session and mark dead an agent the runtime forgot."""
        db = self.rt.db
        if db is None:
            return {"ok": False, "error": f"no agent named {agent_id}"}
        rows = db.execute(
            """
            SELECT agent_id, session FROM agents
             WHERE (agent_id = ? OR agent_id = ?)
               AND status NOT IN ('done', 'dead')
            """,
            (agent_id, _crow_handler_companion(agent_id)),
        ).fetchall()
        if not rows:
            return {"ok": False, "error": f"no agent named {agent_id}"}
        for row in rows:
            session = row["session"]
            if session and await tmux.session_exists(session):
                with contextlib.suppress(tmux.TmuxError):
                    await tmux.kill_session(session)
            _db_set_agent_status(db, row["agent_id"], AgentStatus.DEAD.value)
            # Forensic gap the v1 left open: a force-stop of an agent the runtime
            # forgot left no trace. Ride the one bus aspect into agent_records
            # (no-op when the recorder is off / no bus).
            emit = getattr(self.rt, "_emit_agent_lifecycle", None)
            if emit is not None:
                emit(
                    op="force_stop",
                    agent_id=row["agent_id"],
                    details={"session": session, "requested_agent_id": agent_id},
                )
        return {"handled": True, "agent_id": agent_id}

    async def rename_rogue_agent(self, agent_id: str, name: str) -> dict[str, Any]:
        """Rename a live rogue crow without restarting its harness."""
        if not is_rogue_agent_id(agent_id):
            return {"ok": False, "error": "rename is only supported for rogue crows"}
        agent = self.rt.get_agent(agent_id)
        if agent is None:
            return {"ok": False, "error": f"no agent named {agent_id}"}
        match = re.match(r"^(.+)-rogue-(.+)$", agent_id)
        if match is None:
            return {"ok": False, "error": f"cannot parse rogue agent id {agent_id}"}
        prefix = match.group(1)
        slug = self._rogue_slug(name)
        new_agent_id = f"{prefix}-rogue-{slug}"
        if new_agent_id == agent_id:
            return {"handled": True, "agent_id": agent_id}
        if self.rt.get_agent(new_agent_id) is not None:
            return {"ok": False, "error": f"agent already exists: {new_agent_id}"}

        old_session = getattr(agent, "session", None)
        new_session = format_session_name(self.rt, "crow", f"_{prefix}_rogue_{slug}")
        if (
            isinstance(old_session, str)
            and old_session != new_session
            and await tmux.session_exists(new_session)
        ):
            return {"ok": False, "error": f"session already exists: {new_session}"}

        renamed = self.rt.rename_agent(
            agent_id,
            new_agent_id,
            persist=self.rt.sync_agent,
        )
        if renamed is None:
            return {"ok": False, "error": f"failed to rename {agent_id}"}
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
                return {"ok": False, "error": f"no agent named {agent_id}"}
            harness_session = getattr(agent, "harness_session", None)
            if harness_session is None:
                return {"ok": False, "error": f"agent {agent_id} has no harness session"}
            await harness_session.interrupt()
            return {"handled": True}
        if not agent_id.startswith("crow-"):
            return {"ok": False, "error": "interrupt is only supported for crow agents"}
        ticket_id = agent_id[len("crow-") :]
        if not ticket_id:
            return {"ok": False, "error": "crow agent_id requires a ticket id"}
        handler = self.rt.get_crow_handler(ticket_id)
        if handler is None:
            return {"ok": False, "error": f"no crow_handler for {ticket_id}"}
        await handler.interrupt_crow()
        return {"handled": True}


__all__ = ["AgentOps"]
