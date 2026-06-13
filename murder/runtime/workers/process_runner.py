"""Small multiprocessing runner for subprocess-isolated workers."""

from __future__ import annotations

import multiprocessing as mp
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

ProcessTarget = Callable[[Any, Any], None]


@dataclass
class SubprocessWorkerRunner:
    """Own a single spawned worker process.

    The target receives ``(stop_event, command_queue)``. Worker-specific
    bootstrap stays outside this generic runner so Collaborator, UsageProbe,
    and per-ticket Crow workers can each choose their own dependencies.
    """

    target: ProcessTarget
    name: str
    args: tuple[Any, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        self._ctx = mp.get_context("spawn")
        self._stop = None
        self._commands = None
        self._process: mp.Process | None = None

    @property
    def pid(self) -> int | None:
        process = self._process
        return process.pid if process is not None else None

    @property
    def is_alive(self) -> bool:
        process = self._process
        return bool(process is not None and process.is_alive())

    async def start(self) -> None:
        if self.is_alive:
            return
        self._stop = self._ctx.Event()
        self._commands = self._ctx.Queue()
        self._stop.clear()
        self._process = self._ctx.Process(
            target=self.target,
            args=(self._stop, self._commands, *self.args),
            name=self.name,
        )
        self._process.start()

    async def dispatch(self, command: object) -> None:
        if self._commands is None:
            raise RuntimeError(f"worker process {self.name!r} is not started")
        self._commands.put(command)

    async def stop(self, timeout_s: float) -> None:
        process = self._process
        if process is None:
            return
        if self._stop is None:
            raise RuntimeError(f"worker process {self.name!r} has no stop event")
        self._stop.set()
        process.join(timeout_s)
        if process.is_alive():
            process.terminate()
            process.join(timeout_s)
        if process.is_alive():
            process.kill()
            process.join()
        self._close_queue()
        self._process = None

    def _close_queue(self) -> None:
        if self._commands is None:
            return
        # The child is dead by the time stop() calls this. If items were still
        # buffered in the feeder thread when the process was killed,
        # join_thread() can block forever — which would wedge supervisor
        # shutdown (it holds the repo flock). cancel_join_thread() detaches the
        # feeder so close() never blocks; abandoning unflushed items is safe
        # since the consumer is gone. Suppress broadly: a hang here is worse
        # than a stray queue error during teardown.
        with suppress(Exception):
            self._commands.cancel_join_thread()
        with suppress(Exception):
            self._commands.close()
        self._commands = None
        self._stop = None
