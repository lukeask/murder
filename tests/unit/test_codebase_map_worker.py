"""CodebaseMapWorker tick logic (t062).

Git + build are stubbed; we drive exactly one tick by having the stubbed
build/idle path set the stop_event so the wait_for loop exits. No perpetual
loop ever runs without stop_event control.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import murder.codebase_map.build as build_mod
import murder.verdict.enforcement.git_diff as git_diff_mod
from murder.runtime.workers.base import WorkerCtx
from murder.runtime.workers.codebase_map_worker import CodebaseMapWorker


class _SummarizerStub:
    pass


def _ctx() -> WorkerCtx:
    return WorkerCtx(repo_root=Path("/repo"), db=sqlite3.connect(":memory:"))


def test_tick_reconciles_at_head(monkeypatch):
    """Each tick reads HEAD and drives ``reconcile_map`` with the repo + db."""
    calls: dict[str, object] = {}

    async def _head(_root):
        return "HEAD_SHA"

    monkeypatch.setattr(git_diff_mod, "head_commit", _head)

    worker = CodebaseMapWorker(interval_s=0.001)
    worker._summarizer = _SummarizerStub()  # bypass client build
    ctx = _ctx()

    async def _run() -> None:
        stop = asyncio.Event()

        async def _reconcile(root, summarizer, *, db, head_sha, concurrency=8):
            calls["reconcile"] = (root, summarizer, db, head_sha)
            stop.set()  # stop after the first tick's work runs

        monkeypatch.setattr(build_mod, "reconcile_map", _reconcile)
        await worker.run(ctx, stop)

    asyncio.run(_run())
    root, summarizer, db, head_sha = calls["reconcile"]
    assert root == ctx.repo_root
    assert summarizer is worker._summarizer
    assert db is ctx.db
    assert head_sha == "HEAD_SHA"


def test_reconcile_error_does_not_kill_worker(monkeypatch):
    """A failing reconcile is logged, not propagated — the worker keeps ticking
    and still exits cleanly on stop_event."""
    ticks = {"n": 0}

    async def _head(_root):
        return "HEAD_SHA"

    monkeypatch.setattr(git_diff_mod, "head_commit", _head)

    worker = CodebaseMapWorker(interval_s=0.001)
    worker._summarizer = _SummarizerStub()
    ctx = _ctx()

    async def _run() -> None:
        stop = asyncio.Event()

        async def _reconcile(*a, **k):
            ticks["n"] += 1
            if ticks["n"] >= 2:
                stop.set()
            raise RuntimeError("boom")

        monkeypatch.setattr(build_mod, "reconcile_map", _reconcile)
        await worker.run(ctx, stop)

    asyncio.run(_run())
    assert ticks["n"] == 2  # survived the first failure, exited on the second


def test_disabled_without_client_does_not_raise_or_spin(monkeypatch):
    """No cheap client -> the worker logs once, idles, never calls git/build,
    and exits cleanly on stop_event (no busy-spin)."""
    import murder.runtime.workers.codebase_map_worker as worker_mod

    monkeypatch.setattr(worker_mod, "_build_client", lambda: None)

    called = {"head": 0}

    async def _head(_root):  # pragma: no cover - must not be reached
        called["head"] += 1
        return "X"

    monkeypatch.setattr(git_diff_mod, "head_commit", _head)

    worker = CodebaseMapWorker(interval_s=0.001)
    ctx = _ctx()

    async def _run() -> None:
        stop = asyncio.Event()
        # First tick: client build returns None -> disabled. Stop after a couple
        # of ticks to prove it idles without spinning into git/build.
        ticks = {"n": 0}
        orig_ensure = worker._ensure_summarizer

        def _ensure_then_count():
            ticks["n"] += 1
            result = orig_ensure()
            if ticks["n"] >= 2:
                stop.set()
            return result

        worker._ensure_summarizer = _ensure_then_count
        await worker.run(ctx, stop)

    asyncio.run(_run())
    assert worker._disabled is True
    assert called["head"] == 0  # never reached git/build


def test_build_client_falls_back_to_auto_free(monkeypatch):
    """No tier mapping -> AutoFreeClient.build_default() is the source."""
    import murder.runtime.workers.codebase_map_worker as worker_mod

    sentinel = object()
    monkeypatch.setattr(
        "murder.llm.clients.auto_free.AutoFreeClient.build_default",
        classmethod(lambda cls: sentinel),
    )
    # Force resolve_tier to find nothing.
    monkeypatch.setattr("murder.user_config.resolve_tier", lambda cfg, role: None)

    assert worker_mod._build_client() is sentinel
