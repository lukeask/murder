"""PlanningHandler — per-planner ASK relay (coroutine, native daemon).

Receives crow ASK events for tickets that belong to this planner's plan,
formats them via prompts/crow_ask_to_planner.md, send_keys into the
planner's tmux session, then polls the pane for >>> ANSWER[<ticket_id>]:
markers and routes each parsed answer back to the asking crow.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from murder.runtime.agents.base import Daemon, AgentRole, AgentStatus
from murder.config import PlannerConfig
from murder.llm.harnesses.base import HarnessAdapter

if TYPE_CHECKING:
    from murder.app.service.runtime_scope import AgentLifecycleHost as Runtime

LOGGER = logging.getLogger(__name__)

# After this many *consecutive* poll-loop failures, publish one ErrorEvent so
# the operator sees a stuck planner relay. The loop keeps running; a clean tick
# resets the counter and re-arms the escalation.
POLL_FAILURE_ESCALATION_THRESHOLD = 5


@dataclass
class PendingAsk:
    ticket_id: str
    ask: str
    crow_session: str
    # A ticket may be asked more than once while a planner send is in flight.
    # This is the identity of one such registration, not a persisted ticket
    # version.  It makes failure compensation conditional on the request that
    # actually failed.
    generation: int


class PlanningHandler(Daemon):
    role = AgentRole.PLANNING_HANDLER

    def __init__(
        self,
        agent_id: str,
        session: str,
        planner_session: str,
        plan_name: str,
        harness: HarnessAdapter,
        config: PlannerConfig,
        *,
        repo_root: Path,
        runtime: Runtime,
    ) -> None:
        self.id = agent_id
        self.session = session
        self.planner_session = planner_session
        self.plan_name = plan_name
        self.harness = harness
        self.config = config
        self.repo_root = Path(repo_root)
        self.runtime = runtime
        self.status = AgentStatus.IDLE
        self.ticket_id = None
        # One in-flight ask per ticket. If the same crow re-asks while a prior
        # ask is pending, the new ask replaces the old pending entry.
        self._pending: dict[str, PendingAsk] = {}
        self._next_pending_generation = 0
        # The pane may contain the same answer on multiple ticks; route each
        # ticket's answer once.
        self._routed: set[str] = set()
        # A carve form persists in the pane across ticks; enqueue its
        # apply-carve-ready command once per (ticket_id, form-hash). The hash
        # lets a *re-carve* (edited form for the same ticket) re-enqueue, while a
        # stable form is enqueued exactly once.
        self._carved: set[str] = set()
        self._log_path: Path | None = None
        self._consecutive_poll_failures = 0

    async def start(self, brief: str, ctx: dict[str, Any]) -> None:
        del brief, ctx
        from murder.bus import StatusChangeEvent
        from murder.state.storage.run_id_allocation import open_pane_log
        from murder.runtime.terminal import tmux

        assert self.runtime.run_id is not None
        self._log_path = open_pane_log(
            self.repo_root, self.runtime.run_id, f"planning_handler_{self.plan_name}"
        )
        self._log_path.write_text(
            f"# planning_handler log for {self.plan_name}\n", encoding="utf-8"
        )
        await tmux.create_session(
            self.session,
            self.repo_root,
            ["tail", "-f", str(self._log_path)],
        )
        self.status = AgentStatus.RUNNING
        self.runtime.sync_agent(self)
        if self.runtime.bus and self.runtime.run_id:
            await self.runtime.bus.publish(
                StatusChangeEvent(
                    run_id=self.runtime.run_id,
                    agent_id=self.id,
                    role=self.role,
                    ticket_id=None,
                    entity="agent",
                    entity_id=self.id,
                    from_status=AgentStatus.IDLE.value,
                    to_status=AgentStatus.RUNNING.value,
                )
            )

        self._start_loop()

    async def _loop(self) -> None:
        # Startup grace lets the verified observer publish the planner's first
        # persisted frame before this companion begins consuming it.
        if self.config.startup_grace_s > 0:
            await asyncio.sleep(self.config.startup_grace_s)
        while self.status == AgentStatus.RUNNING:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A transient planner pane read failure should not terminate
                # the handler; relay_ask() will surface dead sessions. But a
                # *sustained* failure run is now visible (logged every tick and
                # escalated once on the bus) instead of silently swallowed.
                # If the planner is *genuinely* gone (session absent + agent
                # marked dead in the DB), self-terminate quietly instead.
                if await self._planner_is_gone():
                    LOGGER.info(
                        "planning_handler %s: planner gone — stopping handler quietly",
                        self.plan_name,
                    )
                    await self.stop()
                    return
                await self._record_poll_failure(exc)
            else:
                self._consecutive_poll_failures = 0
            await asyncio.sleep(self.config.poll_interval_s)

    async def _planner_is_gone(self) -> bool:
        """True iff the planner is genuinely gone (no tmux session AND dead/absent in DB).

        Distinguishes a real teardown (the planner was murdered — ctrl+m — or
        died) from a transient pane-capture blip. Only when *both* signals agree
        do we treat the planner as gone and self-terminate, so a momentary tmux
        hiccup never tears down a live relay.
        """
        from murder.runtime.terminal import tmux

        try:
            if await tmux.session_exists(self.planner_session):
                return False
        except Exception:
            # Can't tell — be conservative and keep relaying.
            return False
        # Session is absent. Confirm via the DB that the planner agent is dead or
        # gone before quietly self-terminating.
        db = getattr(self.runtime, "db", None)
        if db is None:
            return True
        from murder.runtime.agents.base import AgentStatus as _AS
        from murder.state.persistence.agents import get_agent_status

        planner_agent_id = f"planner-{self.plan_name}"
        try:
            status = get_agent_status(db, planner_agent_id)
        except Exception:
            return True
        if status is None:
            return True
        return status in (_AS.DEAD.value, _AS.DONE.value, _AS.FAILED.value)

    async def _record_poll_failure(self, exc: Exception) -> bool:
        """Account one poll-loop failure. Returns True iff an ErrorEvent was published."""
        self._consecutive_poll_failures += 1
        LOGGER.warning(
            "planning_handler %s poll tick failed (%d consecutive): %s",
            self.plan_name,
            self._consecutive_poll_failures,
            exc,
        )
        if self._consecutive_poll_failures != POLL_FAILURE_ESCALATION_THRESHOLD:
            # Only escalate on the threshold-crossing tick; a reset re-arms it.
            return False
        if not (self.runtime.bus and self.runtime.run_id):
            return False
        from murder.bus import ErrorEvent

        await self.runtime.bus.publish(
            ErrorEvent(
                run_id=self.runtime.run_id,
                agent_id=self.id,
                role=self.role,
                ticket_id=None,
                message=(
                    f"planning_handler for plan {self.plan_name} has failed "
                    f"{self._consecutive_poll_failures} consecutive poll ticks: {exc}"
                ),
                recoverable=True,
            )
        )
        return True

    async def stop(self, *, failed: bool = False, kill_session: bool = True) -> None:
        del kill_session
        from murder.runtime.terminal import tmux

        if failed or self.status == AgentStatus.FAILED:
            self.status = AgentStatus.FAILED
        else:
            self.status = AgentStatus.DONE
        await super().stop(failed=failed)
        with contextlib.suppress(Exception):
            await tmux.kill_session(self.session)

    async def relay_ask(self, ticket_id: str, ask: str, crow_session: str) -> None:
        """Called by orchestrator when a crow on this plan emits an ASK."""
        from murder.llm.prompts import render

        template = self.config.crow_ask_template.removesuffix(".md")
        try:
            body = render(template, ticket_id=ticket_id, ask=ask)
        except Exception:
            body = (
                f"A crow working on ticket {ticket_id} has a question:\n\n"
                f"{ask}\n\n"
                f"Please wrap your reply as `>>> ANSWER[{ticket_id}]: <reply>` "
                "so the system can extract it."
            )
        get_agent = getattr(self.runtime, "get_agent", None)
        planner = get_agent(f"planner-{self.plan_name}") if callable(get_agent) else None
        if planner is None:
            raise RuntimeError(f"no live planner for plan {self.plan_name}")
        previous, was_routed, pending = self._register_pending_ask(
            ticket_id, ask, crow_session
        )
        try:
            result = await planner.send(body)
        except Exception:
            self._restore_pending_after_failed_delivery(
                pending, previous, was_routed
            )
            raise
        if not result.ok:
            self._restore_pending_after_failed_delivery(
                pending, previous, was_routed
            )
            raise RuntimeError(result.message or "planner message delivery failed")

    def _register_pending_ask(
        self, ticket_id: str, ask: str, crow_session: str
    ) -> tuple[PendingAsk | None, bool, PendingAsk]:
        """Install routing state before crossing the planner-send boundary.

        The returned old entry is deliberately kept only by the caller that
        installed the replacement.  That caller may restore it after a failed
        delivery, but only if no later request superseded its generation.
        """
        self._next_pending_generation += 1
        pending = PendingAsk(
            ticket_id=ticket_id,
            ask=ask,
            crow_session=crow_session,
            generation=self._next_pending_generation,
        )
        previous = self._pending.get(ticket_id)
        was_routed = ticket_id in self._routed
        self._pending[ticket_id] = pending
        # A new question is entitled to a new answer for this ticket.
        self._routed.discard(ticket_id)
        return previous, was_routed, pending

    def _restore_pending_after_failed_delivery(
        self,
        pending: PendingAsk,
        previous: PendingAsk | None,
        was_routed: bool,
    ) -> None:
        """Undo *this* failed registration without disturbing a replacement."""
        if self._pending.get(pending.ticket_id) != pending:
            return
        if previous is None:
            del self._pending[pending.ticket_id]
        else:
            self._pending[pending.ticket_id] = previous
        if was_routed:
            self._routed.add(pending.ticket_id)
        else:
            self._routed.discard(pending.ticket_id)

    def _complete_pending_ask(self, pending: PendingAsk) -> bool:
        """Complete a routed ask iff it has not been superseded while awaiting."""
        if self._pending.get(pending.ticket_id) != pending:
            return False
        self._routed.add(pending.ticket_id)
        del self._pending[pending.ticket_id]
        return True

    async def tick(self) -> None:
        get_agent = getattr(self.runtime, "get_agent", None)
        planner = get_agent(f"planner-{self.plan_name}") if callable(get_agent) else None
        ingested = getattr(planner, "latest_ingested_frame", None) if planner is not None else None
        pane = getattr(getattr(ingested, "frame", None), "raw_text", None)
        if not isinstance(pane, str):
            return
        from murder.runtime.orchestration.verified_signals import VerifiedOrchestrationSignals

        signals = VerifiedOrchestrationSignals.from_ingested(ingested)

        for ticket_id, reply in signals.answers:
            if ticket_id in self._routed:
                continue
            entry = self._pending.get(ticket_id)
            if entry is None:
                # Stale answer from before restart, or a planner mis-tag.
                continue
            crow = self.runtime.get_crow(ticket_id)
            if crow is not None:
                with contextlib.suppress(Exception):
                    await crow.send(reply)
            self._complete_pending_ask(entry)

        await self._scan_carve_forms(signals.assistant_text)

    async def _scan_carve_forms(self, pane: str) -> None:
        """Detect the planner's YAML carve forms and enqueue apply-carve-ready.

        Mirrors the ANSWER-marker scan: the carve form lingers in the pane, so
        each (ticket_id, form) is enqueued once. The orchestrator worker handler
        is idempotent (a duplicate apply on an already-ready ticket is a no-op),
        which backstops any hash collision or restart re-scan.
        """
        import hashlib

        from murder.work.tickets.carve_scan import detect_carve_forms

        if self.runtime.db is None or self.runtime.run_id is None:
            return

        for spec in detect_carve_forms(pane):
            ticket_id = str(spec["id"])
            form_hash = hashlib.sha256(repr(sorted(spec.items())).encode()).hexdigest()[:16]
            marker = f"{ticket_id}:{form_hash}"
            if marker in self._carved:
                continue
            try:
                self._enqueue_carve_ready(ticket_id, spec, form_hash)
            except Exception as exc:
                LOGGER.warning(
                    "planning_handler %s failed to enqueue carve-ready for %s: %s",
                    self.plan_name,
                    ticket_id,
                    exc,
                )
                continue
            self._carved.add(marker)

    def _enqueue_carve_ready(self, ticket_id: str, spec: dict[str, Any], form_hash: str) -> None:
        from uuid import uuid4

        from murder.state.persistence.commands import enqueue_command
        from murder.runtime.orchestration.commands import OrchestrationCommand
        from murder.runtime.orchestration.worker_names import WorkerName

        assert self.runtime.db is not None and self.runtime.run_id is not None
        command_id = str(uuid4())
        # Idempotency key keyed on the ticket + form content: a re-scan of the
        # SAME form collapses (unique index drops the duplicate), while an edited
        # carve form for the same ticket re-enqueues. The orchestrator apply is
        # itself idempotent against an already-ready row.
        idempotency_key = f"ticket.apply_carve_ready:{self.runtime.run_id}:{ticket_id}:{form_hash}"
        enqueue_command(
            self.runtime.db,
            command_id=command_id,
            run_id=self.runtime.run_id,
            agent_id=self.id,
            role=self.role.value if hasattr(self.role, "value") else str(self.role),
            ticket_id=ticket_id,
            target_worker=WorkerName.ORCHESTRATOR,
            kind=OrchestrationCommand.TICKET_APPLY_CARVE_READY,
            payload={"ticket_id": ticket_id, "carve": spec},
            correlation_id=command_id,
            idempotency_key=idempotency_key,
        )
