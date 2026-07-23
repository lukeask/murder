"""CompletionCoordinator — runs checks and dispatches resolution actions."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from murder.runtime.agents.types import AgentRole
from murder.runtime.orchestration.events import CompletionVerdictEvent
from murder.runtime.orchestration.notifier import OrchestrationNotifier

from .checks.base import CheckResult, CheckStatus, CompletionContext
from .persistence import bump_attempts, get_attempts, reset_attempts, write_check_result
from .policy import Owner, resolution_policy
from .registry import CheckRegistry

if TYPE_CHECKING:
    from murder.runtime.agents.base import Agent
    from murder.runtime.orchestration.outcome import TicketOutcomeService
    from murder.verdict.escalations.service import EscalationService
    from murder.work.tickets.status import TicketStatus

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DoneHandleResult:
    completed: bool
    failed_checks: tuple[str, ...] = ()


class CoordinatorHost(Protocol):
    """Subset of runtime the coordinator requires."""

    repo_root: Path
    db: sqlite3.Connection | None
    bus: OrchestrationNotifier | None
    run_id: str | None

    def get_crow(self, ticket_id: str) -> Agent | None: ...

    def get_agent(self, agent_id: str) -> Agent | None: ...

    # F1: key-only ticket snapshot emit (async choke point; see Runtime).

EnsurePlanner = Callable[[str], Awaitable[str]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _get_plan_name(conn: sqlite3.Connection, ticket_id: str) -> str | None:
    has_plan_tickets = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'plan_tickets'"
    ).fetchone()
    table = "plan_tickets" if has_plan_tickets is not None else "plan_related_tickets"
    row = conn.execute(
        f"SELECT plan_name FROM {table} WHERE ticket_id = ? LIMIT 1",
        (ticket_id,),
    ).fetchone()
    return str(row["plan_name"]) if row else None


class CompletionCoordinator:
    def __init__(
        self,
        rt: CoordinatorHost,
        registry: CheckRegistry,
        *,
        ensure_planning_agent: EnsurePlanner | None = None,
    ) -> None:
        self._rt = rt
        self._registry = registry
        self._ensure_planning_agent = ensure_planning_agent

    async def handle_done(
        self,
        ticket_id: str,
        *,
        crow_session: str,
        repo_root: Path | None = None,
    ) -> DoneHandleResult:
        from murder.state.persistence.tickets import get_ticket as _db_get_ticket

        if self._rt.db is None:
            return DoneHandleResult(completed=False)

        conn = self._rt.db
        row = _db_get_ticket(conn, ticket_id)
        if row is None:
            LOGGER.warning("coordinator.handle_done: ticket %s not found", ticket_id)
            return DoneHandleResult(completed=False)

        ctx = CompletionContext(
            ticket_id=ticket_id,
            repo_root=repo_root or self._rt.repo_root,
            db=conn,
        )

        checks = self._registry.assigned_checks(row)
        timestamp = _now()

        results: list[tuple[str, CheckResult | None]] = []
        for check in checks:
            try:
                result = await check.run(ctx)
            except Exception as exc:
                LOGGER.error("check %s raised: %s", check.name, exc)
                write_check_result(conn, ticket_id, check.name, timestamp, "fail", None)
                results.append((check.name, None))
                continue

            data_json = json.dumps({"message": result.message, "hint": result.hint})
            write_check_result(conn, ticket_id, check.name, timestamp, result.status.value, data_json)
            results.append((check.name, result))

        passes = [(name, r) for name, r in results if r is not None and r.status == CheckStatus.PASS]
        failures = [(name, r) for name, r in results if r is None or r.status == CheckStatus.FAIL]

        for name, _ in passes:
            reset_attempts(conn, ticket_id, name)

        if not failures:
            await self._transition_done(ticket_id)
            await self._emit_verdict(ticket_id, completed=True)
            return DoneHandleResult(completed=True)

        reprompt_msgs: list[str] = []
        ticket_failed = False
        for name, result in failures:
            n = get_attempts(conn, ticket_id, name)
            bump_attempts(conn, ticket_id, name)
            owner = resolution_policy(name, n)
            ticket_failed = await self._dispatch(
                owner,
                ticket_id=ticket_id,
                check_name=name,
                result=result,
                crow_session=crow_session,
                reprompt_msgs=reprompt_msgs,
            )
            if ticket_failed:
                # The ticket is now terminal. Stop dispatching remaining checks
                # and never send a reprompt to a crow whose ticket has failed.
                break

        if not ticket_failed and reprompt_msgs:
            crow = self._rt.get_crow(ticket_id)
            if crow is not None:
                combined = "The following checks failed. Please fix them:\n\n" + "\n\n".join(reprompt_msgs)
                await crow.send(combined)

        failed_check_names = tuple(name for name, _ in failures)
        await self._emit_verdict(
            ticket_id,
            completed=False,
            ticket_failed=ticket_failed,
            failed_checks=list(failed_check_names),
        )
        return DoneHandleResult(
            completed=False,
            failed_checks=failed_check_names,
        )

    async def _emit_verdict(
        self,
        ticket_id: str,
        *,
        completed: bool,
        ticket_failed: bool = False,
        failed_checks: list[str] | None = None,
    ) -> None:
        """Publish the completion verdict so forensic capture rides the bus aspect.

        The event's ``record_family = "decision_records"`` class var lets the
        recorder subscriber route it into the ``decision_records`` store off the
        one bus aspect. This is now server-side forensic data only. No-op
        before the bus / run id exist.
        """
        if self._rt.bus is None or self._rt.run_id is None:
            return
        await self._rt.bus.publish(
            CompletionVerdictEvent(
                run_id=self._rt.run_id,
                agent_id="completion",
                ticket_id=ticket_id,
                completed=completed,
                ticket_failed=ticket_failed,
                failed_checks=failed_checks or [],
            )
        )

    async def _dispatch(
        self,
        owner: Owner,
        *,
        ticket_id: str,
        check_name: str,
        result: CheckResult | None,
        crow_session: str,
        reprompt_msgs: list[str],
    ) -> bool:
        """Dispatch one resolution action. Returns True iff the ticket was failed."""
        if owner == Owner.REPROMPT:
            if result is not None:
                msg = result.hint or result.message
            else:
                msg = f"Check '{check_name}' failed with an internal error."
            reprompt_msgs.append(f"[{check_name}] {msg}")

        elif owner == Owner.ASK_PLANNER:
            await self._ask_planner(ticket_id, check_name, result, crow_session)

        elif owner == Owner.ASK_USER:
            if self._rt.db is None:
                return False
            reason = self._format_failure_reason(check_name, result)
            await self._escalate_to_user(ticket_id, reason)
            await self._block_ticket(ticket_id)

        elif owner == Owner.FAIL_TICKET:
            reason = self._format_failure_reason(check_name, result)
            await self._fail_ticket(ticket_id, reason)
            return True

        return False

    async def _ask_planner(
        self,
        ticket_id: str,
        check_name: str,
        result: CheckResult | None,
        crow_session: str,
    ) -> None:
        if self._rt.db is None:
            return

        plan_name = _get_plan_name(self._rt.db, ticket_id)
        if plan_name is None:
            reason = self._format_failure_reason(check_name, result)
            await self._escalate_to_user(ticket_id, f"[no plan] {reason}")
            return

        if self._ensure_planning_agent is not None:
            try:
                await self._ensure_planning_agent(plan_name)
            except Exception as exc:
                LOGGER.warning("ensure_planning_agent failed for %s: %s", plan_name, exc)

        from murder.runtime.agents.planning_handler import PlanningHandler

        handler = self._rt.get_agent(f"planning_handler-{plan_name}")
        if not isinstance(handler, PlanningHandler):
            reason = self._format_failure_reason(check_name, result)
            LOGGER.warning("no live planner for plan %s — escalating to user", plan_name)
            await self._escalate_to_user(ticket_id, f"[no planner] {reason}")
            return

        question = (
            f"The following check failed for ticket {ticket_id}. "
            f"What should the crow do to resolve it?\n\n"
            f"Check: {check_name}\n"
            f"{self._format_failure_reason(check_name, result)}"
        )
        await handler.relay_ask(ticket_id, question, crow_session)

    def _format_failure_reason(self, check_name: str, result: CheckResult | None) -> str:
        if result is None:
            return f"Check '{check_name}' failed with an internal error."
        if result.hint:
            return f"{result.message}: {result.hint}"
        return result.message

    async def _transition_done(self, ticket_id: str) -> None:
        """Complete the ticket via the shared terminal-transition authority.

        The normalize-then-complete walk (READY/PLANNED/BLOCKED -> IN_PROGRESS ->
        DONE), the already-done idempotency guard, and the F1 snapshot emit all
        live in ``TicketOutcomeService.complete_ticket`` so the done path no
        longer hand-rolls a status event the orchestrator already encapsulates.
        """
        if self._rt.db is None:
            return
        await self._outcomes().complete_ticket(ticket_id)

    async def _fail_ticket(self, ticket_id: str, reason: str) -> None:
        """Fail the ticket via the shared terminal-transition authority."""
        if self._rt.db is None:
            return
        await self._outcomes().fail_ticket(ticket_id, reason)

    async def _emit_status(
        self, ticket_id: str, from_status: TicketStatus | str, to_status: str
    ) -> None:
        """Emit the typed StatusChangeEvent + key-only ticket snapshot.

        The coordinator's terminal transitions bypass
        ``orchestrator._emit_ticket_status`` (different component, different
        agent_id attribution), so this is the coordinator-flavoured equivalent:
        ``agent_id="coordinator"`` plus the F1 snapshot beside the typed event.
        No-op before the bus / run id exist.
        """
        from murder.runtime.orchestration.events import StatusChangeEvent
        from murder.work.tickets.status import TicketStatus

        if self._rt.bus is None or self._rt.run_id is None:
            return
        from_s = from_status.value if isinstance(from_status, TicketStatus) else from_status
        await self._rt.bus.publish(
            StatusChangeEvent(
                run_id=self._rt.run_id,
                agent_id="coordinator",
                role=AgentRole.COLLABORATOR,
                ticket_id=ticket_id,
                entity="ticket",
                entity_id=ticket_id,
                from_status=from_s,
                to_status=to_status,
            )
        )

    def _outcomes(self) -> TicketOutcomeService:
        """Build the shared terminal-transition service, coordinator-flavoured.

        Wires ``emit_status`` to the coordinator's own ``agent_id="coordinator"``
        emitter and the worktree prune / escalation recording to the coordinator
        host, so completion and failure reuse the same authority the orchestrator
        does instead of forking it.
        """
        from murder.runtime.orchestration.outcome import TicketOutcomeService

        assert self._rt.db is not None
        return TicketOutcomeService(
            conn=self._rt.db,
            repo_root=self._rt.repo_root,
            escalations=self._make_escalation_service(),
            emit_status=self._emit_status,
        )

    async def _escalate_to_user(self, ticket_id: str, reason: str) -> None:
        esc = self._make_escalation_service()
        await esc.escalate_to_user(reason, severity=2, ticket_id=ticket_id)

    async def _block_ticket(self, ticket_id: str) -> None:
        if self._rt.db is None:
            return
        from murder.work.tickets.status import TicketStatus
        from murder.state.persistence.tickets import update_ticket_status
        update_ticket_status(self._rt.db, ticket_id, TicketStatus.BLOCKED.value)
        self._rt.db.commit()

    def _make_escalation_service(self) -> EscalationService:
        from murder.verdict.escalations.service import EscalationService

        assert self._rt.db is not None
        return EscalationService(
            conn=self._rt.db,
            repo_root=self._rt.repo_root,
            bus=self._rt.bus,
            run_id=self._rt.run_id,
            agent_id="coordinator",
            role=AgentRole.COLLABORATOR,
        )


__all__ = ["CompletionCoordinator", "CoordinatorHost", "DoneHandleResult"]
