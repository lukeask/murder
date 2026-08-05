"""Process-wide resources whose lifetime matches one RepositoryHost.

Owns RepoDb, run/log infrastructure, advanced-log, orchestration event sink,
command submitter, recorder subscription, and external-stop signals.
Does not own agents, sessions, sync, documents, dispatchers, or exclusivity
locking (the daemon manager owns one host per repository_id).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import signal
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from murder.observability.advanced_log import (
    AdvancedLogBase,
    ArtifactRefRecord,
    NullAdvancedLog,
    open_advanced_log,
    set_current_advanced_log,
)
from murder.observability.log_context import set_run_id
from murder.observability.logging_setup import (
    configure_logging,
    resolve_log_level,
    resolve_recorder_mode,
)
from murder.runtime.orchestration.command_repository import (
    PersistingCommandSubmitter,
    SqliteCommandRepository,
)
from murder.runtime.orchestration.events import OrchestrationEvent
from murder.runtime.orchestration.notifier import (
    InProcessOrchestrationEventSink,
    SubscriptionHandle,
)
from murder.runtime.orchestration.ports import CommandSubmitter, OrchestrationEventSink
from murder.state.persistence.connection import RepoDb, open_repo_db
from murder.state.persistence.runs import end_run as _db_end_run
from murder.state.persistence.runs import insert_run as _db_insert_run
from murder.state.persistence.runs import (
    set_run_advanced_log_path as _db_set_run_advanced_log_path,
)
from murder.state.storage.paths import logs_dir, panes_dir, service_log
from murder.state.storage.run_id_allocation import allocate_run_id

if TYPE_CHECKING:
    from murder.config import Config


@dataclass(frozen=True)
class ProcessResources:
    db: RepoDb
    run_id: str
    events: OrchestrationEventSink
    commands: CommandSubmitter
    advanced_log: AdvancedLogBase


class ProcessScope:
    """Acquire and tear down per-repository Service resources."""

    def __init__(self) -> None:
        self._resources: ProcessResources | None = None
        self._external_stop = asyncio.Event()
        self._stack = AsyncExitStack()
        self._closed = False
        self._repo_root: Path | None = None
        self._recorder_sub: SubscriptionHandle | None = None
        self._recorder_mode: str = "off"

    @classmethod
    @asynccontextmanager
    async def open(
        cls,
        config: Config,
        repo_root: Path,
    ) -> AsyncIterator[ProcessScope]:
        scope = cls()
        await scope._stack.__aenter__()
        try:
            await scope._acquire(config, repo_root)
        except BaseException:
            # Preserve the acquisition failure; secondary cleanup errors stay quiet.
            with contextlib.suppress(Exception):
                await scope.close()
            raise
        try:
            yield scope
        finally:
            await scope.close()

    async def _acquire(self, config: Config, repo_root: Path) -> None:
        self._external_stop.clear()
        self._repo_root = repo_root
        self._closed = False

        db = open_repo_db(repo_root)
        self._stack.callback(db.close)

        run_id = allocate_run_id(repo_root)
        set_run_id(run_id)
        configure_logging(
            level=resolve_log_level(),
            log_path=service_log(repo_root, run_id),
        )
        snap = json.dumps(config.model_dump(mode="json"), default=str)
        _db_insert_run(db, run_id, snap)

        async def _end_run() -> None:
            with contextlib.suppress(Exception):
                _db_end_run(db, run_id)

        self._stack.push_async_callback(_end_run)

        mode = resolve_recorder_mode()
        self._recorder_mode = mode
        advanced_log = open_advanced_log(repo_root, run_id, mode)
        set_current_advanced_log(advanced_log)

        async def _stop_advanced_log() -> None:
            with contextlib.suppress(Exception):
                await advanced_log.stop()
            set_current_advanced_log(NullAdvancedLog())

        # Register before start so a failed/partial start still clears ambient
        # context and attempts stop on unwind.
        self._stack.push_async_callback(_stop_advanced_log)
        await advanced_log.start()

        advanced_log.write_session_info(main_db=db)
        if mode != "off":
            with contextlib.suppress(Exception):
                _db_set_run_advanced_log_path(
                    db, run_id, str(advanced_log.db_path or "")
                )
        for artifact in (
            service_log(repo_root, run_id),
            logs_dir(repo_root) / "supervisor.ndjson",
            panes_dir(repo_root, run_id),
        ):
            size: int | None = None
            with contextlib.suppress(OSError):
                if artifact.exists():
                    size = artifact.stat().st_size
            advanced_log.record_artifact_ref(
                ArtifactRefRecord(
                    path=str(artifact),
                    size=size,
                    sha=None,
                    links={"run_id": run_id},
                )
            )

        events = InProcessOrchestrationEventSink()
        commands = PersistingCommandSubmitter(SqliteCommandRepository(db), events)

        if mode != "off":

            async def _record(event: OrchestrationEvent) -> None:
                advanced_log.record_orchestration_event(event)

            self._recorder_sub = events.subscribe(_record)

            def _cancel_recorder() -> None:
                if self._recorder_sub is not None:
                    self._recorder_sub.cancel()
                    self._recorder_sub = None

            self._stack.callback(_cancel_recorder)

        self._resources = ProcessResources(
            db=db,
            run_id=run_id,
            events=events,
            commands=commands,
            advanced_log=advanced_log,
        )

    @property
    def resources(self) -> ProcessResources:
        if self._resources is None:
            raise RuntimeError("ProcessScope is not open")
        return self._resources

    @property
    def db(self) -> RepoDb:
        return self.resources.db

    @property
    def run_id(self) -> str:
        return self.resources.run_id

    @property
    def events(self) -> OrchestrationEventSink:
        return self.resources.events

    @property
    def commands(self) -> CommandSubmitter:
        return self.resources.commands

    @property
    def advanced_log(self) -> AdvancedLogBase:
        return self.resources.advanced_log

    @property
    def recorder_mode(self) -> str:
        return self._recorder_mode

    def is_external_stop_set(self) -> bool:
        return self._external_stop.is_set()

    def clear_external_stop(self) -> None:
        """Clear the signal so an imminent stop is authoritative, not graceful."""
        self._external_stop.clear()

    async def wait_for_signal(self) -> None:
        """Block until SIGINT/SIGTERM. Used after CLI kickoff / by the daemon."""
        loop = asyncio.get_running_loop()

        def _wake() -> None:
            self._external_stop.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, _wake)
        await self._external_stop.wait()

    async def close(self) -> None:
        """Idempotent teardown of process resources.

        Propagates the first cleanup failure from ``AsyncExitStack.aclose``
        (which already continues secondary callbacks). Idempotent thereafter.
        """
        if self._closed:
            return
        self._closed = True
        try:
            await self._stack.aclose()
        finally:
            self._resources = None
            self._recorder_sub = None


__all__ = ["ProcessResources", "ProcessScope"]
