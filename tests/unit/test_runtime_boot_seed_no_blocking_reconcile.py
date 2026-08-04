"""Boot-path no longer blocks on a full markdown->DB reconcile (perf change).

`Runtime.start` used to `await self._sync.reconcile_all()` — a full blocking
markdown->DB scan — right before spawning sync loops. It now calls the cheap,
idempotent `self._sync.seed()` instead; the heavy scan is carried by the five
background loops `FilesystemSyncService.start()` launches (each loop's `run()`
reconciles on entry). Sync loops start after parse-error notifier injection
(`Runtime.start_filesystem_sync`).

Invariants covered:
  1. `Runtime.start` calls `self._sync.seed()` exactly once on the boot path.
  2. `Runtime.start` does NOT await `self._sync.reconcile_all()` on the boot
     path — the service-level full scan is never invoked synchronously
     during `start()`.
  3. Sync loops start via `start_filesystem_sync()` (after notifier injection
     in the host path).
  4. (Preserve-behavior) `FilesystemSyncService.reconcile_all()` still seeds
     (calls `seed()` / `seed_examples`), so the shutdown path is unaffected.

Style note: the project convention is `asyncio.run()` over pytest-asyncio. We
drive a real `Runtime.start()` (the actual code under test) with the heavy
collaborators left real (DB + flock on a tmp repo_root, NullAdvancedLog when
the recorder is off) and tmux faked via the `fake_tmux` fixture. Only the
`_sync` service is replaced with a thin recording spy so we can assert the
exact boot-path call sequence without launching the real perpetual loops
(conftest noop-patches asyncio.sleep, so a real start would busy-spin).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from murder.app.service.filesystem_sync import FilesystemSyncService
from murder.app.service.recovery import ReconcileReport
from murder.app.service.runtime import Runtime
from murder.config import (
    Config,
    CrowHandlerConfig,
    HarnessRoleConfig,
    ProjectConfig,
)

EXPECTED_DISPATCH_TICKS = 2


def _config() -> Config:
    return Config(
        project=ProjectConfig(name="repo"),
        collaborator=HarnessRoleConfig(harness="codex"),
        default_crow=HarnessRoleConfig(harness="codex"),
        crow_handler=CrowHandlerConfig(model="test-model"),
    )


class _SpySupervisor:
    """Thin stand-in for FilesystemSyncService recording the boot-path calls.

    `start` records without launching the real five perpetual sync loops
    (conftest noop-patches asyncio.sleep, which would turn each loop into a
    busy-spin). `reconcile_all` records that it was called but does nothing
    else, so if the boot path ever awaited it we would see it in `calls` —
    letting us assert it is absent.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        # Sub-syncs Runtime.start reads off the service after attach().
        self.plan_sync = MagicMock()
        self.note_sync = MagicMock()
        self.notetaker_context_sync = MagicMock()
        self.ticket_sync = MagicMock()
        self.report_sync = MagicMock()
        self.seed_count = 0
        self.start_count = 0
        self.reconcile_count = 0

    def seed(self) -> None:
        self.calls.append("seed")
        self.seed_count += 1

    async def start(self) -> None:
        self.calls.append("start")
        self.start_count += 1

    async def reconcile_all(self) -> None:
        # Records invocation; called only from the shutdown path (close()).
        self.calls.append("reconcile_all")
        self.reconcile_count += 1

    async def close(self) -> None:
        # Mirror the real close contract: reconcile once on the way down.
        await self.reconcile_all()


def test_runtime_start_seeds_without_blocking_reconcile(
    fake_tmux, repo_root: Path, monkeypatch
) -> None:
    spy = _SpySupervisor()

    def _fake_attach(*_args, **_kwargs) -> _SpySupervisor:
        return spy

    monkeypatch.setattr(
        "murder.app.service.runtime.FilesystemSyncService.attach",
        _fake_attach,
    )

    rt = Runtime(_config(), repo_root)

    async def _drive() -> tuple[list[str], list[str]]:
        await rt.start()
        # Snapshot the boot-path call sequence BEFORE start_filesystem_sync /
        # shutdown, so the assertions reflect start() in isolation.
        boot_calls = list(spy.calls)
        await rt.start_filesystem_sync()
        after_sync = list(spy.calls)
        await rt.stop()
        return boot_calls, after_sync

    boot_calls, after_sync = asyncio.run(_drive())

    # 1. seed() called exactly once on the boot path.
    assert spy.seed_count == 1
    # 3. start() called via start_filesystem_sync (heavy scan carried there).
    assert spy.start_count == 1
    # 2. reconcile_all() was NOT awaited synchronously during start().
    assert "reconcile_all" not in boot_calls
    assert boot_calls == ["seed"]
    assert after_sync == ["seed", "start"]


def test_activity_dispatcher_starts_after_reconcile_and_is_cancelled_on_stop(
    fake_tmux, repo_root: Path, monkeypatch
) -> None:
    order: list[str] = []
    spy = _SpySupervisor()
    entered_second_tick = asyncio.Event()

    def _fake_attach(*_args, **_kwargs) -> _SpySupervisor:
        return spy

    def _reconcile(*_args, **_kwargs) -> ReconcileReport:
        order.append("reconcile")
        return ReconcileReport()

    class _Dispatcher:
        calls = 0

        async def tick(self) -> None:
            self.calls += 1
            order.append(f"tick:{self.calls}")
            assert "reconcile" in order
            if self.calls == 1:
                raise RuntimeError("transient")
            entered_second_tick.set()
            await asyncio.Event().wait()

    dispatcher = _Dispatcher()
    rt = Runtime(
        _config(),
        repo_root,
        activity_dispatcher_factory=lambda _db, _registry=None: _make_dispatcher(),
    )

    def _make_dispatcher() -> _Dispatcher:
        order.append("factory")
        assert rt.startup_reconcile_report is not None
        assert rt.run_id is not None
        assert rt.orchestration_events is not None
        assert rt._sync is spy
        return dispatcher

    monkeypatch.setattr(
        "murder.app.service.runtime.FilesystemSyncService.attach",
        _fake_attach,
    )
    monkeypatch.setattr(
        "murder.app.service.runtime.reconcile_agents_vs_tmux",
        _reconcile,
    )

    async def _drive() -> asyncio.Task[None]:
        await rt.start()
        assert rt._dispatchers is not None
        task = rt._dispatchers._tasks["phase4-activities"]  # noqa: SLF001
        await asyncio.wait_for(entered_second_tick.wait(), timeout=1)
        await rt.stop()
        return task

    task = asyncio.run(_drive())

    assert order[:2] == ["reconcile", "factory"]
    assert dispatcher.calls == EXPECTED_DISPATCH_TICKS
    assert task.cancelled()


def test_supervisor_seed_is_idempotent_and_calls_seed_examples(
    repo_root: Path, monkeypatch
) -> None:
    """`FilesystemSyncService.seed()` wraps the idempotent seed_examples."""
    seed_calls: list[Path] = []

    def _fake_seed_examples(root: Path) -> None:
        seed_calls.append(root)

    monkeypatch.setattr(
        "murder.app.service.filesystem_sync.seed_examples",
        _fake_seed_examples,
    )

    service = FilesystemSyncService(
        plan_sync=MagicMock(),
        note_sync=MagicMock(),
        notetaker_context_sync=MagicMock(),
        ticket_sync=MagicMock(),
        report_sync=MagicMock(),
        repo_root=repo_root,
    )

    service.seed()
    service.seed()

    # seed() routes to seed_examples each call (idempotency lives in
    # seed_examples itself) and passes the repo root.
    assert seed_calls == [repo_root, repo_root]


def test_supervisor_reconcile_all_still_seeds(repo_root: Path) -> None:
    """Preserve-behavior: reconcile_all() seeds first (shutdown path intact)."""
    order: list[str] = []

    class _RecordingDocSync:
        def __init__(self, name: str) -> None:
            self._name = name

        async def reconcile_all(self) -> None:
            order.append(f"reconcile:{self._name}")

    service = FilesystemSyncService(
        plan_sync=_RecordingDocSync("plan"),
        note_sync=_RecordingDocSync("note"),
        notetaker_context_sync=_RecordingDocSync("notetaker_context"),
        ticket_sync=_RecordingDocSync("ticket"),
        report_sync=_RecordingDocSync("report"),
        repo_root=repo_root,
    )

    # Spy on seed() to confirm reconcile_all calls it before the per-category
    # scans (and exactly once).
    seeded: list[int] = []
    original_seed = service.seed

    def _tracked_seed() -> None:
        seeded.append(len(order))
        order.append("seed")
        original_seed()

    service.seed = _tracked_seed  # type: ignore[method-assign]

    asyncio.run(service.reconcile_all())

    assert order[0] == "seed", "reconcile_all must seed before scanning"
    assert order.count("seed") == 1
    # All five per-category reconciles run after the seed.
    assert order[1:] == [
        "reconcile:plan",
        "reconcile:note",
        "reconcile:notetaker_context",
        "reconcile:ticket",
        "reconcile:report",
    ]
