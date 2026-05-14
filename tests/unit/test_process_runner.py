from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from murder.workers.process_runner import SubprocessWorkerRunner


def _process_runner_target(stop: Any, commands: Any) -> None:
    while not stop.is_set():
        try:
            command = commands.get(timeout=0.02)
        except Exception:
            continue
        if isinstance(command, str) and command.startswith("touch:"):
            Path(command.removeprefix("touch:")).write_text("ok", encoding="utf-8")


@pytest.mark.asyncio
async def test_subprocess_worker_runner_dispatches_and_stops(tmp_path: Path) -> None:
    marker = tmp_path / "marker.txt"
    runner = SubprocessWorkerRunner(_process_runner_target, name="process-test")

    await runner.start()
    assert runner.pid is not None
    assert runner.is_alive

    await runner.dispatch(f"touch:{marker}")
    deadline = time.monotonic() + 2
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.02)

    await runner.stop(1)

    assert marker.read_text(encoding="utf-8") == "ok"
    assert not runner.is_alive
