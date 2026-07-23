from __future__ import annotations

import asyncio
import sqlite3
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from murder.runtime.orchestration.commands import OrchestrationCommand
from murder.runtime.orchestration.events import CommandEvent
from murder.runtime.orchestration.worker_names import WorkerName


@dataclass(frozen=True)
class WorkerSpec:
    name: WorkerName
    accepts: tuple[OrchestrationCommand, ...] = ()
    # "thread" is a historical label: these workers are NOT run on a dedicated
    # thread. The supervisor schedules them as cooperative asyncio tasks on its
    # single event loop (see supervisor.py), so their run/on_command bodies must
    # not block (heavy synchronous SQLite/subprocess work stalls the bus, the
    # heartbeat loop, and the UI). Only "subprocess" workers get a real
    # SubprocessWorkerRunner with process isolation.
    process_model: Literal["thread", "subprocess"] = "thread"
    heartbeat_s: float = 5.0
    shutdown_grace_s: float = 2.0


@dataclass(frozen=True)
class WorkerCommand:
    name: OrchestrationCommand
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkerCtx:
    repo_root: Path
    db: sqlite3.Connection | None = None
    run_id: str | None = None
    shutdown: asyncio.Event | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    on_heartbeat: Callable[[str], Awaitable[None]] | None = None


class Worker(ABC):
    def __init__(self, spec: WorkerSpec) -> None:
        self.spec = spec

    @property
    def name(self) -> WorkerName:
        return self.spec.name

    @property
    def accepts(self) -> tuple[OrchestrationCommand, ...]:
        return self.spec.accepts

    async def on_start(self, ctx: WorkerCtx) -> None:
        return None

    @abstractmethod
    async def run(self, ctx: WorkerCtx, stop_event: asyncio.Event) -> None:
        raise NotImplementedError

    async def on_stop(self, ctx: WorkerCtx) -> None:
        return None

    async def handle_command(self, command: WorkerCommand, ctx: WorkerCtx) -> bool:
        return False

    async def on_command(self, command: CommandEvent, ctx: WorkerCtx) -> dict[str, Any]:
        """Handle a routed command and report the outcome as a result dict.

        The dispatcher reads the result via a three-way contract:

        - ``handled`` absent or ``True`` (no ``ok`` key, or ``ok: True``) →
          success → the command is marked completed.
        - ``{"ok": False, "error": <str>}`` → domain failure (the handler ran
          but hit a normal business error) → the command is marked failed with
          that error.
        - ``{"handled": False}`` → wiring miss only (this worker has no branch
          for the command kind) → the command is failed AND logged at ERROR,
          because it indicates a routing bug, not a runtime condition.
        """
        handled = await self.handle_command(
            WorkerCommand(command.kind, command.payload),
            ctx,
        )
        return {"handled": handled}
