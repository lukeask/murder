"""Bootstrap registers the codebase-map worker as the 8th worker (t062)."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import murder.app.service.bootstrap as bootstrap_mod
from murder.runtime.workers import CodebaseMapWorker


class _RecordingSupervisor:
    def __init__(self, ctx, *, command_dispatcher=None):
        self.ctx = ctx
        self.started: list[object] = []

    async def start_worker(self, worker):
        self.started.append(worker)


def test_codebase_map_worker_registered(monkeypatch):
    monkeypatch.setattr(bootstrap_mod, "Supervisor", _RecordingSupervisor)
    monkeypatch.setattr(bootstrap_mod, "CommandDispatcher", lambda **k: object())

    db = sqlite3.connect(":memory:")
    runtime = SimpleNamespace(db=db, run_id="run-1", get_agent=lambda *_a: None)
    orchestrator = SimpleNamespace(
        ensure_collaborator=lambda *_a: None,
    )

    # HarnessVersionProbeWorker.from_runtime + UsageProbeWorker.from_worker_ctx
    # need real scaffolding; stub the heavyweight worker factories to plain
    # objects so the test focuses on registration order/membership.
    from murder.runtime import workers as workers_pkg

    monkeypatch.setattr(
        workers_pkg.UsageProbeWorker, "from_worker_ctx", classmethod(lambda cls, ctx: object())
    )
    monkeypatch.setattr(
        bootstrap_mod, "UsageProbeWorker",
        SimpleNamespace(from_worker_ctx=lambda ctx: object()),
    )
    monkeypatch.setattr(
        bootstrap_mod, "HarnessVersionProbeWorker",
        SimpleNamespace(from_runtime=lambda rt: object()),
    )

    supervisor = asyncio.run(
        bootstrap_mod.start_supervisor_workers(
            repo_root=Path("/repo"),
            runtime=runtime,
            orchestrator=orchestrator,
            broker=object(),
        )
    )

    map_workers = [w for w in supervisor.started if isinstance(w, CodebaseMapWorker)]
    assert len(map_workers) == 1
    # Registered last, after all pre-existing workers.
    assert isinstance(supervisor.started[-1], CodebaseMapWorker)
