"""Agent ABC + lifecycle.

All roles implement this interface. `CrowAgent`, `CollaboratorAgent`, and
`PlanningAgent` own real tmux sessions (interactive harness) and subclass
`HarnessBackedAgent`. `CrowHandler` and `PlanningHandler` are coroutine
daemons that subclass `Daemon` directly. They own no *interactive* harness
pane and have no transcript; their tmux session is just a non-interactive
``tail -f`` of their handler log file (so the session is attachable for
debugging), created in start() and killed in stop().
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

# Re-export from bus to keep StrEnum definitions in one place.
from murder.bus import AgentStatus
from murder.bus import Role as AgentRole

LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from murder.llm.harnesses.base import HarnessAdapter
    from murder.llm.harnesses.results import SimpleResult

__all__ = ["LifecycleParticipant", "HarnessBackedAgent", "Daemon", "AgentRole", "AgentStatus"]
TRANSCRIPT_SCROLLBACK_LINES = 4000

# Cadence of the service-owned transcript projection ticker. Hash-skip makes
# idle ticks essentially free, so a single fixed interval is fine for all
# agents (ticketed crows, rogues, collaborators, planners).
PROJECTION_INTERVAL_S = 0.4

# Agent statuses at which projection should stop (terminal — session gone).
TERMINAL_STATUSES = (AgentStatus.DONE, AgentStatus.FAILED, AgentStatus.DEAD)


class LifecycleParticipant(ABC):
    id: str
    role: AgentRole
    session: str  # tmux session name (interactive) or virtual session (logfile-tail)
    status: AgentStatus
    ticket_id: str | None  # None for Collaborator, PlanningAgent, PlanningHandler

    @abstractmethod
    async def start(self, brief: str, ctx: dict[str, Any]) -> None:
        """Bring the agent online. For Crows: spawn tmux + harness;
        send the system prompt. For native daemons: kick off the loop."""

    @abstractmethod
    async def stop(self, *, failed: bool = False, kill_session: bool = True) -> None:
        """Shut down. Idempotent.

        kill_session=False leaves the tmux session alive so a subsequent
        Runtime.start() can detect and reattach (graceful TUI quit path).
        """

    @abstractmethod
    async def send(self, msg: str) -> SimpleResult[None]:
        """Deliver a message to the agent. For Crows: send-keys via harness.
        For PlanningAgent: send-keys via harness. For handlers: ignore by default."""

    async def is_live(self) -> bool:
        """Return True if the agent session is currently running.

        HarnessBackedAgent overrides this to check tmux session existence.
        Daemon subclasses use status. Default is True so non-overriding
        participants are considered live unless explicitly stopped.
        """
        return True

    async def tick(self) -> None:
        """Optional cadence hook. CrowHandler uses this for its poll loop;
        others noop."""
        return None

    def attach_hint(self) -> str:
        from murder.runtime.terminal.tmux import attach_command

        return attach_command(self.session)


class HarnessBackedAgent(LifecycleParticipant):
    """Lifecycle participant that owns an interactive harness pane in tmux.

    Provides the single server-side conversation path shared by
    CollaboratorAgent, PlanningAgent, and CrowAgent: the service
    parses the harness pane here — never in the TUI — and stores blocks into
    the JSON conversation store. ``conversation_id`` is the agent ``id`` (one
    live conversation per agent).
    """

    harness: HarnessAdapter
    harness_session: Any  # HarnessSession — typed Any to avoid import cycle
    # Owned transcript projection. Every harness-backed agent projects its own
    # pane into the conversation store via project_once(), driven by a single
    # service-owned ticker (ServiceHost). This is a universal per-agent concern,
    # independent of ticket orchestration — so it lives here rather than in
    # CrowHandler, which keeps rogues (no handler) and collaborators projecting
    # too. The producer is built (no I/O, no task) by start_conversation().
    _producer: Any = None
    # Busy-harness chat queue: a user message accepted while the pane was not
    # input-ready, held for delivery at the next awaiting_input projection tick.
    # Mirrored into conversations.queued_message (DB-owns-runtime) and pushed to
    # clients via ConversationStateEvent so the TUI can render the queued line.
    _queued_message: str | None = None
    # The last (live_state, queued_message) pair pushed over the bus, so the
    # projection tick only publishes on change (not 2.5Hz).
    _last_pushed_conv_state: Any = None
    # Set by live-session usage sampling while a slash-command overlay is open;
    # projection ticks skip pane capture until the overlay is dismissed.
    usage_capture_in_progress: bool = False
    # The verified control runtime is initialized only after tmux startup, when
    # it can bind one immutable observer and one serialized actuator to the
    # real pane.  It replaces procedural prompt delivery during migration.
    verified_harness_control: Any | None = None
    # Exact frame+snapshot pair most recently persisted by the verified
    # observer.  Orchestration companions consume this shared provenance; they
    # never open a second pane-capture path.
    latest_ingested_frame: Any | None = None

    async def initialize_verified_harness_control(self) -> None:
        runtime = getattr(self, "runtime", None)
        if runtime is None or runtime.db is None:
            raise RuntimeError("verified harness control requires the service persistence database")
        from murder.llm.harness_control.runtime.session import VerifiedHarnessControlSession

        options: dict[str, Any] = {}
        if getattr(runtime, "verified_prompt_driver_policy", None) is not None:
            options["prompt_policy"] = runtime.verified_prompt_driver_policy
        if getattr(runtime, "verified_prompt_driver_sleep", None) is not None:
            options["prompt_sleep"] = runtime.verified_prompt_driver_sleep
        self.verified_harness_control = VerifiedHarnessControlSession.from_tmux(
            harness_kind=self.harness.kind,
            terminal_session=self.session,
            connection=runtime.db,
            persistence_session_id=self.id,
            **options,
        )
        repository_root = getattr(runtime, "repo_root", getattr(self, "repo_root", None))
        await self.verified_harness_control.ensure_session_controller(
            repository_key=str(repository_root) if repository_root is not None else None,
            agent_key=self.id,
            registry=getattr(runtime, "session_controllers", None),
            recover=True,
        )
        # A reattached pane begins with current evidence, never a resumed
        # procedural stack.  Unfinished persisted effects are explicitly
        # escalated by the session recovery boundary rather than replayed.

    async def send_verified_prompt(
        self,
        text: str,
        *,
        murder_owned: bool = False,
    ) -> SimpleResult[None]:
        """Submit through the persisted controller, never a legacy adapter call."""

        if self.verified_harness_control is None:
            from murder.llm.harnesses.results import fail_result

            return fail_result("verified harness control has not been initialized")
        from murder.llm.harnesses.results import fail_result, ok_result
        from murder.runtime.sessions.contracts import (
            PrincipalKind,
            PrincipalRef,
            SendStructuredMessage,
        )

        del murder_owned
        controller = await self.verified_harness_control.ensure_session_controller()
        try:
            await controller.execute(
                SendStructuredMessage(operation_id=uuid4(), text=text),
                principal=PrincipalRef(kind=PrincipalKind.SERVICE, id=f"agent:{self.id}"),
            )
        except Exception as exc:
            outcome = getattr(exc, "outcome", None)
            outcome_name = getattr(outcome, "name", None)
            if isinstance(outcome_name, str):
                return fail_result(
                    f"verified prompt submission {outcome_name.lower()}"
                )
            return fail_result(f"verified prompt submission failed: {exc}")
        else:
            return ok_result()

    async def interrupt_verified_generation(self) -> bool:
        """Request interruption through the controller's serialized actuator."""

        if self.verified_harness_control is None:
            return False
        from murder.runtime.sessions.contracts import (
            InterruptSession,
            PrincipalKind,
            PrincipalRef,
        )

        controller = await self.verified_harness_control.ensure_session_controller()
        try:
            await controller.execute(
                InterruptSession(operation_id=uuid4()),
                principal=PrincipalRef(kind=PrincipalKind.SERVICE, id=f"agent:{self.id}"),
            )
        except Exception:
            LOGGER.exception("verified session interruption failed for %s", self.id)
            return False
        return True

    async def answer_verified_question(
        self, request: Any, *, operation_id: str | None = None
    ) -> bool:
        """Route a recorded user/policy question decision to verified control."""

        if self.verified_harness_control is None:
            return False
        return cast(
            bool,
            await self._run_verified_session_mutation(
                lambda: self.verified_harness_control.answer_question(
                    request, operation_id=operation_id
                ),
                required_capability="structured_approvals",
            ),
        )

    async def answer_verified_permission(
        self, request: Any, *, operation_id: str | None = None
    ) -> bool:
        """Route a recorded user/policy permission decision to verified control."""

        if self.verified_harness_control is None:
            return False
        return cast(
            bool,
            await self._run_verified_session_mutation(
                lambda: self.verified_harness_control.answer_permission(
                    request, operation_id=operation_id
                ),
                required_capability="structured_approvals",
            ),
        )

    async def select_verified_model(
        self, model: str | None, effort: str | None = None
    ) -> SimpleResult[None]:
        """Select a runtime model through the persisted verified controller.

        A launch option, a picker highlight, or a successful terminal call is
        not treated as activation.  This entry point only succeeds after the
        controller has observed independent active-model readback.
        """

        from murder.llm.harnesses.results import fail_result, ok_result

        if model is None:
            return ok_result()
        if self.verified_harness_control is None:
            return fail_result("verified harness control has not been initialized")
        from murder.llm.harness_control.capabilities.model_selection import (
            ModelSelectionOutcome,
            ModelTarget,
        )

        capabilities = self.harness.parameter_capabilities_for_model(model)
        fast_enabled: bool | None = None
        target_effort = effort
        if capabilities.fast_toggle and effort in {"slow", "fast"}:
            # Composer's advertised slow/fast choice is the state of its Fast
            # checkbox, not an effort row in Cursor's parameter editor.
            fast_enabled = effort == "fast"
            target_effort = None
        result = await self._run_verified_session_mutation(
            lambda: self.verified_harness_control.select_model(
                ModelTarget(model, effort=target_effort, fast_enabled=fast_enabled)
            ),
            required_capability="model_switching",
        )
        if result.outcome is ModelSelectionOutcome.ACTIVATED:
            return ok_result()
        return fail_result(f"verified model selection {result.outcome.name.lower()}")

    async def collect_verified_usage(self, *, trigger: str) -> Any | None:
        """Collect live usage inside the same serialized session mailbox."""

        if self.verified_harness_control is None:
            return None
        return await self._run_verified_session_mutation(
            lambda: self.verified_harness_control.collect_usage(trigger=trigger),
            required_capability="structured_messages",
        )

    async def _run_verified_session_mutation(
        self,
        effect: Any,
        *,
        required_capability: Any,
    ) -> Any:
        """Bridge pre-existing typed reducers into the Phase 2 mailbox."""

        if self.verified_harness_control is None:
            raise RuntimeError("verified harness control has not been initialized")
        from murder.runtime.sessions.contracts import PrincipalKind, PrincipalRef

        controller = await self.verified_harness_control.ensure_session_controller()
        return await controller.run_internal(
            uuid4(),
            effect,
            principal=PrincipalRef(kind=PrincipalKind.SERVICE, id=f"agent:{self.id}"),
            required_capability=required_capability,
        )

    async def terminate_verified_session(self, *, force: bool = False) -> bool:
        """Terminate the live pane through its serialized controller."""

        if self.verified_harness_control is None:
            return False
        from murder.runtime.sessions.contracts import (
            PrincipalKind,
            PrincipalRef,
            TerminateSession,
        )

        controller = await self.verified_harness_control.ensure_session_controller()
        try:
            await controller.execute(
                TerminateSession(
                    operation_id=uuid4(),
                    force=force,
                    reason="agent lifecycle stop",
                ),
                principal=PrincipalRef(kind=PrincipalKind.SERVICE, id=f"agent:{self.id}"),
            )
        except Exception:
            LOGGER.exception("verified session termination failed for %s", self.id)
            return False
        await self.verified_harness_control.remove_session_controller()
        return True

    async def _usage_sampling_context(self) -> Any | None:
        runtime = getattr(self, "runtime", None)
        if runtime is None:
            return None
        from murder.llm.harnesses.usage_sampling import UsageSamplingContext

        config = getattr(runtime, "config", None)
        repo_root = getattr(runtime, "repo_root", None) or getattr(self, "repo_root", None)
        db = getattr(runtime, "db", None)
        if config is None or repo_root is None:
            return None
        return UsageSamplingContext(config=config, repo_root=Path(repo_root), db=db)

    async def _sample_live_usage_on_startup(self) -> None:
        ctx = await self._usage_sampling_context()
        if ctx is None:
            return
        from murder.llm.harnesses.usage_sampling import sample_live_session_usage

        await sample_live_session_usage(self, ctx, "agent_startup")

    async def _sample_live_usage_on_shutdown(self) -> None:
        ctx = await self._usage_sampling_context()
        if ctx is None or ctx.db is None:
            return
        from murder.llm.harnesses.usage_sampling import sample_live_session_usage

        await sample_live_session_usage(self, ctx, "agent_shutdown")

    async def is_live(self) -> bool:
        from murder.runtime.terminal import tmux

        return await tmux.session_exists(self.session)

    def start_conversation(self) -> None:
        """Reset conversation state for a fresh harness session: drop the prior
        run's transcript so a new session never surfaces stale chat, and build a
        fresh producer (the single per-conversation parser). Called from each
        subclass's start()."""
        runtime = getattr(self, "runtime", None)
        self.latest_ingested_frame = None
        if runtime is not None and runtime.db is not None:
            from murder.state.persistence import conversation

            conversation.clear(runtime.db, self.id)
        self._build_producer()

    def _build_producer(self) -> None:
        """Build a fresh ConversationProducer (no I/O, no background task).
        No-op without a db — projection would have nowhere to land."""
        runtime = getattr(self, "runtime", None)
        if runtime is None or runtime.db is None:
            self._producer = None
            return
        from murder.runtime.agents.conversation_producer import ConversationProducer

        self._producer = ConversationProducer(
            conversation_id=self.id,
            harness_kind=self.harness.kind,
            system_prompt=self.harness.system_prompt,
            db=runtime.db,
            publish=self._publish_conversation_block,
        )

    async def project_once(self) -> None:
        """Capture the pane and project it into the conversation store once.

        Driven by the service-owned projection ticker (one loop for all agents),
        never by a per-agent background task — so it carries no surprising
        side-effects for unit tests that merely start an agent. Producer
        hash-skip makes unchanged ticks cheap; a missing session (still starting
        or torn down) surfaces as a TmuxError, which the caller swallows."""
        if self._producer is None or self.status in TERMINAL_STATUSES:
            return
        if self.usage_capture_in_progress:
            return
        if self.verified_harness_control is None:
            return
        runtime = getattr(self, "runtime", None)
        ingested = await self.verified_harness_control.ingest_once()
        self.latest_ingested_frame = ingested
        decision_router = getattr(runtime, "structured_decisions", None)
        if decision_router is not None:
            await decision_router.observe(self, ingested.snapshot)
        transcript = next(
            (
                item.payload.get("transcript")
                for item in ingested.evidence
                if isinstance(item.payload.get("transcript"), dict)
            ),
            None,
        )
        if transcript is None:
            raise RuntimeError("verified harness evidence omitted its transcript document")
        projection = await self._producer.poll_document(transcript)
        await self._emit_plan_resort_if_planner(projection.changed)
        await self._route_projected_orchestration_signals(projection.changes)
        # Process-lifecycle status: reconcile agents.status (the crows-panel
        # spinner) with the harness's working↔idle signal (BUG-13).
        self._sync_lifecycle_status()
        # Busy-harness chat queue: deliver once the parser reports the pane is
        # input-ready, and push the (live_state, queued) pair to clients when it
        # changed (cheap no-op otherwise — the publish is change-gated).
        await self._deliver_queued_if_ready()
        await self._publish_conversation_state()

    async def _route_projected_orchestration_signals(self, changes: tuple[Any, ...]) -> None:
        """Give a ticket Crow's handler the assistant blocks that just changed.

        Projection is the authoritative ingress boundary for assistant text.
        Routing these concrete block changes avoids reinterpreting an accumulated
        pane transcript on every handler polling tick.
        """
        if not changes or self.role is not AgentRole.CROW or not self.ticket_id:
            return
        runtime = getattr(self, "runtime", None)
        get_handler = getattr(runtime, "get_crow_handler", None)
        handler = get_handler(self.ticket_id) if callable(get_handler) else None
        observe = getattr(handler, "observe_conversation_changes", None)
        if callable(observe):
            await observe(changes)

    def _sync_lifecycle_status(self) -> None:
        """Toggle ``agents.status`` RUNNING↔IDLE to match the harness signal.

        The crows-panel spinner is derived from ``agents.status == 'running'``,
        but an agent only set RUNNING on startup and DONE/FAILED on stop — there
        was no per-turn transition, so a finished crow stayed "running" forever
        (BUG-13). The parser's live state is the harness-agnostic working/idle
        signal (``working`` vs ``awaiting_input``/``awaiting_approval``), so this
        works for every harness kind.

        Edge-triggered: syncs only when the target status differs from the
        current one, so it does not spam the bus on every poll tick. Strictly a
        RUNNING↔IDLE toggle — an agent in any other status (blocked, escalating,
        done, failed, dead) is left untouched so a stale state read can never
        clobber an escalation or resurrect a stopped agent.
        """
        runtime = getattr(self, "runtime", None)
        if runtime is None:
            return
        state = self._current_live_state()
        if state is None:
            return
        target = AgentStatus.RUNNING if state == "working" else AgentStatus.IDLE
        if self.status not in (AgentStatus.RUNNING, AgentStatus.IDLE):
            return
        if self.status == target:
            return
        self.status = target
        runtime.sync_agent(self)

    async def _emit_plan_resort_if_planner(self, had_changes: bool) -> None:
        """F11 H1: emit the key-only ``plan`` re-sort invalidation, gated.

        The conversation rebuild (``project_parsed_doc_with_changes`` ->
        ``replace_agent_messages``) bumps a planner's MAX(captured_at), which is the
        ordering key for ``get_plans_snapshot`` — so a planner's transcript growth
        re-sorts the plans list with no plans-table write. Emit ``plan`` ONLY for a
        planner AND ONLY when this poll produced real block changes (the producer
        hash-skips unchanged panes, so an idle planner polled by the service ticker
        yields no changes and emits nothing). This bounds ``plan`` invalidations to
        genuine transcript growth, not the poll cadence. Content itself rides
        ``conversation.block``; this is purely the list re-sort.
        """
        if not had_changes or not self.id.startswith("planner-"):
            return
        return

    @property
    def pending_message(self) -> str | None:
        """The queued-but-undelivered chat message, if any."""
        return self._queued_message

    async def queue_message(self, msg: str) -> dict[str, Any]:
        """Deliver ``msg`` now if the harness pane is input-ready; else queue it.

        The queued message is held until the projection tick sees the parser
        report ``awaiting_input`` (see :meth:`project_once`), so a busy crow —
        including one showing a multiple-choice dialog (``awaiting_approval``)
        — never has chat typed into the wrong surface. A second message queued
        while one is pending appends (the queue is the not-yet-sent prompt, not
        a mailbox). Returns ``{"queued": bool}`` plus error fields on failure,
        matching the CrowHandler contract.

        Idleness is judged by the parser's live state — the same source that
        drives delivery (:meth:`_deliver_queued_if_ready`) and the TUI's
        ``working`` indicator — so the three can never disagree. The adapter's
        ``is_idle`` pane heuristic is only a fallback for when no state has
        been parsed yet; it is unreliable on harnesses that keep their input
        box visible while working (codex), which would type into a busy pane.
        """
        idle = False
        if self._queued_message is None:
            state = self._current_live_state()
            if state is not None:
                idle = state == "awaiting_input"
        if idle:
            result = await self.send(msg)
            if result is not None and getattr(result, "ok", True) is False:
                return {
                    "queued": False,
                    "ok": False,
                    "error": getattr(result, "message", None) or "message delivery failed",
                }
            return {"queued": False}
        combined = msg if self._queued_message is None else f"{self._queued_message}\n\n{msg}"
        await self._set_queued_message(combined)
        return {"queued": True}

    async def _set_queued_message(self, msg: str | None) -> None:
        """Update the queue in memory + DB and push the state event."""
        self._queued_message = msg
        runtime = getattr(self, "runtime", None)
        if runtime is not None and runtime.db is not None:
            from murder.state.persistence import conversation

            conversation.set_queued_message(runtime.db, self.id, msg)
        await self._publish_conversation_state()

    def _current_live_state(self) -> str | None:
        producer = self._producer
        return getattr(producer, "last_state", None) if producer is not None else None

    async def _publish_conversation_state(self) -> None:
        """Push a ``conversation.state`` event when (live_state, queued) changed."""
        runtime = getattr(self, "runtime", None)
        if runtime is None or runtime.bus is None or runtime.run_id is None:
            return
        state = (self._current_live_state(), self._queued_message)
        if state == self._last_pushed_conv_state:
            return
        self._last_pushed_conv_state = state
        from murder.bus import ConversationStateEvent

        await runtime.bus.publish(
            ConversationStateEvent(
                run_id=str(runtime.run_id),
                agent_id=self.id,
                role=self.role,
                ticket_id=self.ticket_id,
                conversation_id=self.id,
                live_state=state[0],
                queued_message=state[1],
            )
        )

    async def _deliver_queued_if_ready(self) -> None:
        """Send the queued message once the parser reports ``awaiting_input``.

        Called from the projection tick (after the producer poll updated
        ``last_state``). Clears the queue first so a delivery failure surfaces
        as a notice rather than a silent every-tick retry storm.
        """
        if self._queued_message is None or self._current_live_state() != "awaiting_input":
            return
        queued = self._queued_message
        await self._set_queued_message(None)
        result = await self.send(queued)
        if result is not None and getattr(result, "ok", True) is False:
            await self.record_notice_block_event(
                f"queued message delivery failed: {getattr(result, 'message', None) or 'unknown error'}"
            )

    def record_user_block(self, text: str) -> None:
        """Record a ground-truth ``user`` turn at the send boundary.

        The service knows the exact text it received, so it stores it
        authoritatively rather than re-deriving it from a noisy pane capture
        (the source of the collaborator corruption). No-op without a db.
        """
        runtime = getattr(self, "runtime", None)
        if runtime is None or runtime.db is None:
            return
        from murder.state.persistence import conversation

        conversation.append_user_message(runtime.db, self.id, text)

    async def record_user_block_event(self, text: str) -> None:
        """Record and push a ground-truth ``user`` block."""
        runtime = getattr(self, "runtime", None)
        if runtime is None or runtime.db is None:
            return
        from murder.state.persistence import conversation

        block = conversation.append_user_message(runtime.db, self.id, text)
        if block is not None:
            await self._publish_conversation_block(
                "block-appended",
                conversation.block_to_wire(block),
            )

    async def _publish_conversation_block(self, action: str, block: dict[str, Any]) -> None:
        runtime = getattr(self, "runtime", None)
        if runtime is None or runtime.bus is None or runtime.run_id is None:
            return
        from murder.bus import ConversationBlockEvent

        await runtime.bus.publish(
            ConversationBlockEvent(
                run_id=str(runtime.run_id),
                agent_id=self.id,
                role=self.role,
                ticket_id=self.ticket_id,
                conversation_id=self.id,
                action=action,  # type: ignore[arg-type]
                block=block,
            )
        )

    async def record_notice_block_event(self, message: str, *, severity: str = "error") -> None:
        """Record and push a service-originated notice block."""
        runtime = getattr(self, "runtime", None)
        if runtime is None or runtime.db is None:
            return
        from murder.state.persistence import conversation

        block = conversation.append_notice(
            runtime.db,
            self.id,
            message,
            severity=severity,
        )
        if block is not None:
            await self._publish_conversation_block(
                "block-appended",
                conversation.block_to_wire(block),
            )

    async def _projected_doc(self) -> dict[str, Any] | None:
        """Run one producer-backed projection tick, then return the canonical
        persisted conversation doc.

        On-demand refreshes share the single server-side parser/persistence path
        with the service ticker (:meth:`project_once`) rather than re-parsing the
        pane through a second accumulator. ``project_once`` already hash-skips an
        unchanged pane, no-ops on a terminal/producerless agent, updates
        ``last_state``, delivers a ready queued message, and emits the planner
        re-sort — so the refresh inherits all of that and can never diverge from
        the hot path. A dead pane surfaces as a swallowed ``TmuxError``, leaving
        the last persisted doc intact. Returns ``None`` (caller renders an empty
        doc) only when there is no db to read from.
        """
        runtime = getattr(self, "runtime", None)
        if runtime is None or runtime.db is None:
            return None
        from murder.runtime.terminal import tmux

        with contextlib.suppress(tmux.TmuxError):
            await self.project_once()
        from murder.state.persistence import conversation

        return conversation.read_conversation_doc(runtime.db, self.id)

    async def refresh_transcript_doc(self) -> dict[str, Any]:
        """Project the pane and return the merged rich conversation doc
        (``{"harness","state","condensed","segments"}``) for display. Returns
        an empty doc if there is no persisted conversation yet."""
        doc = await self._projected_doc()
        if doc is None:
            return {
                "harness": self.harness.kind,
                "state": "working",
                "condensed": None,
                "segments": [],
            }
        return doc

    async def _finalize_conversation_on_stop(self, *, kill_session: bool, failed: bool) -> None:
        """Capture harness session id and set conversation status = complete.

        Only runs on clean kills (kill_session=True, failed=False) — the path
        where we're deliberately tearing down a live session.  When
        kill_session=False the session is preserved for reattach; the
        conversation stays in_progress and is marked stale on next startup.
        """
        if not kill_session or failed:
            return
        runtime = getattr(self, "runtime", None)
        if runtime is None or runtime.db is None:
            return
        await self._sample_live_usage_on_shutdown()
        # Do not send a legacy `/exit` command here.  It was an independent
        # terminal writer outside the actuator and only inferred success from a
        # later pane scrape.  Resume identifiers remain harness evidence from
        # normal observations; teardown itself is an explicit session lifecycle
        # operation, not semantic harness input.
        session_id: str | None = None
        from murder.state.persistence import conversation

        if session_id is not None:
            conversation.set_harness_session_id(runtime.db, self.id, session_id)
        # A queued-but-undelivered message dies with the session — clear it so
        # the TUI never renders a stale queued line for a finished agent.
        if self._queued_message is not None:
            with contextlib.suppress(Exception):
                await self._set_queued_message(None)
        conversation.set_conversation_status(runtime.db, self.id, "complete")

    async def refresh_transcript(self) -> list[tuple[str, str]]:
        """Compatibility projection: the effective transcript as ``(role, text)``
        turns (``role`` ∈ ``{"user","assistant"}``), derived from the merged doc.

        Returns ``[]`` if there is no persisted conversation yet (the TUI falls
        back to the raw pane mirror in that case).
        """
        from murder.llm.harnesses.base import _transcript_doc_to_turns

        doc = await self._projected_doc()
        if doc is None:
            return []
        return _transcript_doc_to_turns(doc)


class Daemon(LifecycleParticipant):
    """Lifecycle participant that runs a background poll loop."""

    _poll_task: asyncio.Task[None] | None = None

    def _start_loop(self) -> None:
        self._poll_task = asyncio.create_task(self._loop())

    @abstractmethod
    async def _loop(self) -> None: ...

    async def stop(self, *, failed: bool = False, kill_session: bool = True) -> None:
        del failed, kill_session
        if self._poll_task is not None:
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._poll_task
            self._poll_task = None
        if getattr(self, "runtime", None) is not None and self.runtime.db is not None:
            self.runtime.sync_agent(self)

    async def send(self, msg: str) -> None:
        # Daemons do not own a conversation pane; user/crow chat goes to the
        # paired HarnessBackedAgent, not its handler.
        del msg
