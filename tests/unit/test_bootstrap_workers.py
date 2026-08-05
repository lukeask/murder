"""Bootstrap registers the codebase-map worker as the 8th worker (t062)."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import murder.app.service.bootstrap as bootstrap_mod
from murder.llm.harnesses import usage_sampling
from murder.runtime import workers as workers_pkg
from murder.runtime.workers import CodebaseMapWorker, HarnessVersionProbeWorker, UsageProbeWorker


class _RecordingSupervisor:
    def __init__(self, ctx, *, command_dispatcher=None):
        self.ctx = ctx
        self.started: list[object] = []
        self.stop_all_calls = 0

    async def start_worker(self, worker):
        self.started.append(worker)

    async def stop_all(self):
        self.stop_all_calls += 1


class _FailAfterNSupervisor(_RecordingSupervisor):
    def __init__(self, ctx, *, command_dispatcher=None, fail_after: int = 2):
        super().__init__(ctx, command_dispatcher=command_dispatcher)
        self._fail_after = fail_after

    async def start_worker(self, worker):
        if len(self.started) >= self._fail_after:
            raise RuntimeError("worker start failed")
        await super().start_worker(worker)


def _stub_heavyweight_workers(monkeypatch) -> None:
    # UsageProbeWorker.from_worker_ctx needs real scaffolding; stub the
    # heavyweight worker factory to a plain object so the test focuses on
    # registration order/membership.
    monkeypatch.setattr(
        workers_pkg.UsageProbeWorker, "from_worker_ctx", classmethod(lambda cls, ctx: object())
    )
    monkeypatch.setattr(
        bootstrap_mod,
        "UsageProbeWorker",
        SimpleNamespace(from_worker_ctx=lambda ctx: object()),
    )
    monkeypatch.setattr(
        bootstrap_mod,
        "HarnessVersionProbeWorker",
        lambda **kwargs: object(),
    )


def _bootstrap_kwargs(*, db, harness_versions, agents, orchestrator):
    return dict(
        repo_root=Path("/repo"),
        db=db,
        run_id="run-1",
        events=object(),
        commands=object(),
        advanced_log=object(),
        harness_versions=harness_versions,
        agents=agents,
        orchestrator=orchestrator,
    )


def test_codebase_map_worker_registered(monkeypatch):
    monkeypatch.setattr(bootstrap_mod, "Supervisor", _RecordingSupervisor)
    monkeypatch.setattr(bootstrap_mod, "CommandDispatcher", lambda **k: object())
    _stub_heavyweight_workers(monkeypatch)

    db = sqlite3.connect(":memory:")
    agents = SimpleNamespace(find=lambda *_a: None)
    orchestrator = SimpleNamespace(
        ensure_collaborator=lambda *_a: None,
    )
    harness_versions = SimpleNamespace(replace=lambda *_a, **_k: None)

    supervisor = asyncio.run(
        bootstrap_mod.start_supervisor_workers(
            **_bootstrap_kwargs(
                db=db,
                harness_versions=harness_versions,
                agents=agents,
                orchestrator=orchestrator,
            )
        )
    )

    map_workers = [w for w in supervisor.started if isinstance(w, CodebaseMapWorker)]
    assert len(map_workers) == 1
    # Registered last, after all pre-existing workers.
    assert isinstance(supervisor.started[-1], CodebaseMapWorker)
    assert not hasattr(UsageProbeWorker, "from_runtime")
    assert not hasattr(HarnessVersionProbeWorker, "from_runtime")
    assert not hasattr(usage_sampling, "sample_harness_usages_for_config")
    assert supervisor.ctx.db is db
    assert supervisor.ctx.run_id == "run-1"
    assert supervisor.stop_all_calls == 0


def test_partial_worker_startup_calls_stop_all(monkeypatch):
    fail_after = 2
    captured: list[_FailAfterNSupervisor] = []

    def _supervisor_factory(ctx, *, command_dispatcher=None):
        supervisor = _FailAfterNSupervisor(
            ctx, command_dispatcher=command_dispatcher, fail_after=fail_after
        )
        captured.append(supervisor)
        return supervisor

    monkeypatch.setattr(bootstrap_mod, "Supervisor", _supervisor_factory)
    monkeypatch.setattr(bootstrap_mod, "CommandDispatcher", lambda **k: object())
    _stub_heavyweight_workers(monkeypatch)

    db = sqlite3.connect(":memory:")
    agents = SimpleNamespace(find=lambda *_a: None)
    orchestrator = SimpleNamespace(ensure_collaborator=lambda *_a: None)
    harness_versions = SimpleNamespace(replace=lambda *_a, **_k: None)

    with pytest.raises(RuntimeError, match="worker start failed"):
        asyncio.run(
            bootstrap_mod.start_supervisor_workers(
                **_bootstrap_kwargs(
                    db=db,
                    harness_versions=harness_versions,
                    agents=agents,
                    orchestrator=orchestrator,
                )
            )
        )

    assert len(captured) == 1
    supervisor = captured[0]
    assert len(supervisor.started) == fail_after
    assert supervisor.stop_all_calls == 1
