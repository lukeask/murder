"""Service-owned background work kept out of the composition root."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from murder.llm.harnesses.harnesses_doc import write_harnesses_doc
from murder.llm.harnesses.model_cache import refresh_and_persist_harness_models
from murder.observability.advanced_log import ParserRecord, current_advanced_log
from murder.runtime.agents.base import PROJECTION_INTERVAL_S, HarnessBackedAgent
from murder.runtime.terminal import tmux
from murder.usage_sample_command import run_service_usage_poll_loop

if TYPE_CHECKING:
    from murder.app.service.runtime import Runtime
    from murder.runtime.orchestration.orchestrator import Orchestrator


LOGGER = logging.getLogger(__name__)

# Caps how many surviving-crow reattaches may poll harness/tmux state at once.
# Each reattach can block on a ready-poll for up to 240s, so boot recovery must
# not open an unbounded number of tmux file descriptors at once.
REATTACH_CONCURRENCY = 4


@dataclass
class ServiceBackgroundTasks:
    """Own the service's best-effort polling and startup-recovery tasks.

    The host starts and stops this collaborator as part of process lifecycle;
    this class owns the individual task names, cancellation, and retry policy.
    None of this work is application request handling or composition wiring.
    """

    repo_root: Path
    runtime: Runtime
    orchestrator: Orchestrator
    _tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict, init=False)

    def start(self) -> None:
        """Launch non-blocking work after socket and workers are available."""
        if self.runtime.db is None:
            raise RuntimeError("runtime database is unavailable")
        self._spawn("crow-reattach", self._reattach_surviving_crows())
        self._spawn("startup-rogue-ensure", self._ensure_startup_rogue_safely())
        self._spawn("startup-model-catalog", self._persist_catalog_then_write_models_doc())
        self._spawn(
            "usage-sample-poll",
            run_service_usage_poll_loop(self.repo_root, self.runtime.db),
        )
        self._spawn("transcript-projection-poll", self._run_projection_poll_loop())

    def _spawn(self, name: str, coroutine: Coroutine[object, object, None]) -> None:
        self._tasks[name] = asyncio.create_task(coroutine, name=name)

    async def stop(self) -> None:
        """Cancel and drain all owned work, including one-shot recovery tasks."""
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

    async def _ensure_startup_rogue_safely(self) -> None:
        """Best-effort configured Startup Rogue creation; never fail boot."""
        try:
            await self.orchestrator.ensure_startup_rogue()
        except Exception:
            LOGGER.error("ensure_startup_rogue failed", exc_info=True)

    async def _persist_catalog_then_write_models_doc(self) -> None:
        """Persist configured model catalogs, then write the settings document."""
        await refresh_and_persist_harness_models(self.repo_root, self.runtime.db)
        write_harnesses_doc(self.repo_root)

    async def _run_projection_poll_loop(self) -> None:
        """Project every harness-backed agent pane into the conversation store."""
        # First-failure visibility: a repeating failure otherwise silently
        # freezes live state (and queued-message delivery) for that agent.
        warned_agents: set[str] = set()
        while True:
            for agent in self.runtime.agents.all_agents():
                if not isinstance(agent, HarnessBackedAgent):
                    continue
                try:
                    await agent.project_once()
                except tmux.TmuxError:
                    LOGGER.debug(
                        "projection tick: tmux error for %s (session=%s)",
                        agent.id,
                        getattr(agent, "session", None),
                        exc_info=True,
                    )
                except Exception:
                    if agent.id not in warned_agents:
                        warned_agents.add(agent.id)
                        LOGGER.warning(
                            "projection tick failed for %s (suppressing repeats)",
                            agent.id,
                            exc_info=True,
                        )
                    else:
                        LOGGER.debug("projection tick failed for %s", agent.id, exc_info=True)
                else:
                    warned_agents.discard(agent.id)
                    live_state = agent._current_live_state()
                    queued = agent.pending_message
                    choices = ["<choice-prompt>"] if live_state == "awaiting_approval" else None
                    current_advanced_log().record_parser(
                        ParserRecord(
                            session=getattr(agent, "session", None),
                            live_state=live_state,
                            parsed={"agent_id": agent.id, "queued": queued},
                            choices=choices,
                            dedup_hash=hashlib.sha1(
                                f"{agent.id}|{live_state}|{queued}".encode()
                            ).hexdigest(),
                        )
                    )
            await asyncio.sleep(PROJECTION_INTERVAL_S)

    async def _reattach_surviving_crows(self) -> None:
        """Reattach handlers to surviving crows without delaying socket readiness."""
        report = self.runtime.startup_reconcile_report
        if not report or not report.crows_to_reattach:
            return

        sem = asyncio.Semaphore(REATTACH_CONCURRENCY)

        async def _reattach_one(ticket_id: str, crow_session: str) -> None:
            async with sem:
                try:
                    await self.orchestrator.reattach_crow(ticket_id, crow_session)
                    LOGGER.info(
                        "reattached crow handler for %s (session %s)",
                        ticket_id,
                        crow_session,
                    )
                except Exception:
                    LOGGER.error("failed to reattach crow for %s", ticket_id, exc_info=True)

        try:
            await asyncio.gather(
                *(
                    _reattach_one(ticket_id, crow_session)
                    for ticket_id, crow_session in report.crows_to_reattach
                )
            )
        except Exception:
            LOGGER.error("crow reattach task failed", exc_info=True)


__all__ = ["ServiceBackgroundTasks"]
