"""Agent ABC + lifecycle.

All roles implement this interface. `CrowAgent`, `CollaboratorAgent`, and
`PlanningAgent` own real tmux sessions (interactive harness) and subclass
`HarnessBackedAgent`. `CrowHandler` and `PlanningHandler` are coroutine
daemons that subclass `Daemon` directly; they own no interactive pane and
have no transcript.
"""

from __future__ import annotations

import asyncio
import contextlib
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

# Re-export from bus to keep StrEnum definitions in one place.
from murder.bus import AgentStatus
from murder.bus import Role as AgentRole

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
    CollaboratorAgent, PlanningAgent, and CrowAgent (phase 1.c): the service
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
            with contextlib.suppress(Exception):
                from murder.runtime.terminal import tmux

                await tmux.send_keys(self.session, exit_cmd)
                await asyncio.sleep(0.5)
                pane = await tmux.capture_pane(self.session, lines=40)
                session_id = self.harness.extract_resume_session_id(pane)
        from murder.state.persistence import conversation

        if session_id is not None:
            conversation.set_harness_session_id(runtime.db, self.id, session_id)
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
