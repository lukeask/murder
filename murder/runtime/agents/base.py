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
from typing import TYPE_CHECKING, Any

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
    # Persistent per-conversation accumulator: holds the system prompt + pane
    # scrollback so each refresh feeds incrementally instead of re-parsing 4000
    # lines from scratch. Reset on start_conversation().
    _accumulator: Any = None
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

    async def is_live(self) -> bool:
        from murder.runtime.terminal import tmux

        return await tmux.session_exists(self.session)

    def _ensure_accumulator(self) -> Any:
        """Lazily build the persistent accumulator with the injected system
        prompt (set on the harness at start, so creation is deferred)."""
        if self._accumulator is None:
            from murder.llm.harnesses.transcripts import TranscriptAccumulator

            self._accumulator = TranscriptAccumulator(
                self.harness.kind, system_prompt=self.harness.system_prompt
            )
        return self._accumulator

    def start_conversation(self) -> None:
        """Reset conversation state for a fresh harness session: drop the prior
        run's transcript and the accumulator scrollback so a new session never
        surfaces stale chat. Called from each subclass's start()."""
        self._accumulator = None
        runtime = getattr(self, "runtime", None)
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
        from murder.runtime.terminal import tmux
        from murder.llm.harnesses.transcripts import wants_ansi

        pane = await tmux.capture_pane(
            self.session,
            lines=TRANSCRIPT_SCROLLBACK_LINES,
            escapes=wants_ansi(self.harness.kind),
        )
        had_changes = await self._producer.poll(pane)
        await self._emit_plan_resort_if_planner(had_changes)
        # Busy-harness chat queue: deliver once the parser reports the pane is
        # input-ready, and push the (live_state, queued) pair to clients when it
        # changed (cheap no-op otherwise — the publish is change-gated).
        await self._deliver_queued_if_ready()
        await self._publish_conversation_state()

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
        runtime = getattr(self, "runtime", None)
        if runtime is None:
            return
        from murder.bus.protocol import Entity

        await runtime.publish_snapshot(Entity.PLAN, self.id[len("planner-"):])

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
        from murder.runtime.terminal import tmux

        idle = False
        if self._queued_message is None:
            state = self._current_live_state()
            if state is not None:
                idle = state == "awaiting_input"
            else:
                try:
                    pane = await tmux.capture_pane(self.session, lines=120)
                except tmux.TmuxError:
                    pane = ""
                idle = self.harness.is_idle(pane)
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

    async def _publish_conversation_changes(self, changes: list[Any]) -> None:
        from murder.state.persistence import conversation

        for change in changes:
            await self._publish_conversation_block(
                str(change.action),
                conversation.block_to_wire(change.block),
            )

    async def _project_transcript(self) -> dict[str, Any] | None:
        """Capture the pane, feed the persistent accumulator, and reconcile the
        parsed doc into the JSON store (stripping re-derived user segments in
        favour of ground-truth user blocks).

        Returns the merged conversation doc (ground-truth users + parsed
        non-user segments), or ``None`` if the session is gone. With no db,
        returns the raw parsed doc without persisting.
        """
        from murder.runtime.terminal import tmux
        from murder.llm.harnesses.transcripts import wants_ansi

        try:
            pane = await tmux.capture_pane(
                self.session,
                lines=TRANSCRIPT_SCROLLBACK_LINES,
                escapes=wants_ansi(self.harness.kind),
            )
        except tmux.TmuxError:
            return None
        acc = self._ensure_accumulator()
        runtime = getattr(self, "runtime", None)
        if runtime is not None and runtime.db is not None:
            from murder.state.persistence import conversation as _conv

            acc.user_texts = _conv.read_user_texts(runtime.db, self.id)
        acc.feed(pane)
        doc = acc.to_dict()
        if runtime is None or runtime.db is None:
            return doc
        from murder.state.persistence import conversation

        merged, changes = conversation.project_parsed_doc_with_changes(runtime.db, self.id, doc)
        await self._publish_conversation_changes(changes)
        # F11 H1: same plan re-sort gate as project_once (the producer hot path), so
        # the on-demand refresh path emits the bounded `plan` invalidation too.
        await self._emit_plan_resort_if_planner(bool(changes))
        return merged

    async def refresh_transcript_doc(self) -> dict[str, Any]:
        """Project the pane and return the merged rich conversation doc
        (``{"harness","state","condensed","segments"}``) for display. Returns
        an empty doc if the session is gone."""
        doc = await self._project_transcript()
        if doc is None:
            return {"harness": self.harness.kind, "state": "working",
                    "condensed": None, "segments": []}
        return doc

    async def _finalize_conversation_on_stop(
        self, *, kill_session: bool, failed: bool
    ) -> None:
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
        session_id: str | None = None
        exit_cmd = self.harness.graceful_exit_command()
        if exit_cmd is not None:
            try:
                from murder.runtime.terminal import tmux

                await tmux.send_keys(self.session, exit_cmd)
                await asyncio.sleep(0.5)
                pane = await tmux.capture_pane(self.session, lines=40)
                session_id = self.harness.extract_resume_session_id(pane)
            except Exception:
                # Best-effort: a later /resume will report "no resumable session
                # id"; leave a breadcrumb so the loss isn't silent.
                LOGGER.debug(
                    "resume session-id capture failed for %s", self.id, exc_info=True
                )
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

        Returns ``[]`` if the session is gone (the TUI falls back to the raw
        pane mirror in that case).
        """
        from murder.llm.harnesses.base import _transcript_doc_to_turns

        doc = await self._project_transcript()
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
