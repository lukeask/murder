from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass

from murder.workers.base import WorkerCommand

ThreadTarget = Callable[[threading.Event, "queue.Queue[WorkerCommand]"], None]


@dataclass
class ThreadWorkerRunner:
    target: ThreadTarget
    name: str

    def __post_init__(self) -> None:
        self._stop = threading.Event()
        self._commands: queue.Queue[WorkerCommand] = queue.Queue()
        self._thread: threading.Thread | None = None

    async def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self.target,
            args=(self._stop, self._commands),
            name=self.name,
            daemon=True,
        )
        self._thread.start()

    async def dispatch(self, command: WorkerCommand) -> None:
        self._commands.put(command)

    async def stop(self, timeout_s: float) -> None:
        self._stop.set()
        thread = self._thread
        if thread is None:
            return
        thread.join(timeout_s)
        self._thread = None
