"""Activity and trigger dispatcher retry loops."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from murder.runtime.sessions.registry import SessionControllerRegistry
from murder.state.persistence.connection import RepoDb

if TYPE_CHECKING:
    from murder.runtime.activity_dispatcher import ActivityDispatcher
    from murder.runtime.trigger_dispatcher import TriggerDispatcher

LOGGER = logging.getLogger(__name__)

ActivityDispatcherFactory = Callable[
    [RepoDb, SessionControllerRegistry], "ActivityDispatcher"
]
TriggerDispatcherFactory = Callable[[RepoDb], "TriggerDispatcher"]


@dataclass
class DispatcherLoops:
    """Owns activity/trigger dispatcher instances and their retry loops."""

    activity_dispatcher: ActivityDispatcher | None = None
    trigger_dispatcher: TriggerDispatcher | None = None
    retry_delay_seconds: float = 1.0
    _tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict, init=False, repr=False)
    _shutdown: asyncio.Event = field(default_factory=asyncio.Event, init=False, repr=False)

    @classmethod
    @asynccontextmanager
    async def open(
        cls,
        *,
        db: RepoDb,
        session_controllers: SessionControllerRegistry,
        activity_factory: ActivityDispatcherFactory | None,
        trigger_factory: TriggerDispatcherFactory | None,
        retry_delay_seconds: float = 1.0,
    ) -> AsyncIterator[DispatcherLoops]:
        loops = cls(retry_delay_seconds=retry_delay_seconds)
        await loops.start(
            db=db,
            session_controllers=session_controllers,
            activity_factory=activity_factory,
            trigger_factory=trigger_factory,
        )
        try:
            yield loops
        finally:
            await loops.close()

    async def start(
        self,
        *,
        db: RepoDb,
        session_controllers: SessionControllerRegistry,
        activity_factory: ActivityDispatcherFactory | None,
        trigger_factory: TriggerDispatcherFactory | None,
    ) -> None:
        """Construct optional dispatchers and spawn their retry loops."""
        self._shutdown.clear()
        try:
            if activity_factory is not None:
                self.activity_dispatcher = activity_factory(db, session_controllers)
                self._tasks["phase4-activities"] = asyncio.create_task(
                    self._activity_loop(),
                    name="phase4-activities",
                )
            if trigger_factory is not None:
                self.trigger_dispatcher = trigger_factory(db)
                self._tasks["phase4-triggers"] = asyncio.create_task(
                    self._trigger_loop(),
                    name="phase4-triggers",
                )
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        """Cancel and drain only dispatcher tasks."""
        self._shutdown.set()
        for task in list(self._tasks.values()):
            task.cancel()
        if self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        self.activity_dispatcher = None
        self.trigger_dispatcher = None

    async def _run_tick_loop(
        self,
        name: str,
        tick: Callable[[], Awaitable[object]],
    ) -> None:
        while not self._shutdown.is_set():
            try:
                await tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("%s dispatcher tick failed; retrying", name)
            await asyncio.sleep(self.retry_delay_seconds)

    async def _activity_loop(self) -> None:
        assert self.activity_dispatcher is not None
        await self._run_tick_loop("activity", self.activity_dispatcher.tick)

    async def _trigger_loop(self) -> None:
        assert self.trigger_dispatcher is not None

        async def tick() -> None:
            assert self.trigger_dispatcher is not None
            self.trigger_dispatcher.tick()

        await self._run_tick_loop("trigger", tick)


__all__ = [
    "ActivityDispatcherFactory",
    "DispatcherLoops",
    "TriggerDispatcherFactory",
]
