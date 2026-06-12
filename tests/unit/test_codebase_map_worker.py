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
import murder.codebase_map.store as store_mod
import murder.verdict.enforcement.git_diff as git_diff_mod
from murder.runtime.workers.base import WorkerCtx
from murder.runtime.workers.codebase_map_worker import CodebaseMapWorker


class _SummarizerStub:
    pass


def _ctx() -> WorkerCtx:
    return WorkerCtx(repo_root=Path("/repo"), db=sqlite3.connect(":memory:"))


def test_fresh_build_when_no_map_rows(monkeypatch):
    calls: dict[str, object] = {}

    async def _head(_root):
        return "HEAD_SHA"

    def _latest(_db):
        return None  # no map rows yet

    async def _fresh(root, summarizer, *, db, concurrency=8):
        calls["fresh"] = (root, summarizer, db)

    async def _incr(*a, **k):  # pragma: no cover - must not be called
        calls["incr"] = True

    monkeypatch.setattr(git_diff_mod, "head_commit", _head)
    monkeypatch.setattr(store_mod, "latest_map_sha", _latest)
    monkeypatch.setattr(build_mod, "fresh_build", _fresh)
    monkeypatch.setattr(build_mod, "incremental_update", _incr)

    worker = CodebaseMapWorker(interval_s=0.001)
    worker._summarizer = _SummarizerStub()  # bypass client build
    ctx = _ctx()

    async def _run() -> None:
        stop = asyncio.Event()
        # Stop right after the first tick's work runs.
        orig = _fresh

        async def _fresh_then_stop(*a, **k):
            await orig(*a, **k)
            stop.set()

        monkeypatch.setattr(build_mod, "fresh_build", _fresh_then_stop)
        await worker.run(ctx, stop)

    asyncio.run(_run())
    assert "fresh" in calls
    assert "incr" not in calls


def test_incremental_when_head_differs(monkeypatch):
    calls: dict[str, object] = {}

    async def _head(_root):
        return "NEW_SHA"

    def _latest(_db):
        return "OLD_SHA"

    async def _fresh(*a, **k):  # pragma: no cover - must not be called
        calls["fresh"] = True

    monkeypatch.setattr(git_diff_mod, "head_commit", _head)
    monkeypatch.setattr(store_mod, "latest_map_sha", _latest)
    monkeypatch.setattr(build_mod, "fresh_build", _fresh)

    worker = CodebaseMapWorker(interval_s=0.001)
    worker._summarizer = _SummarizerStub()
    ctx = _ctx()

    async def _run() -> None:
        stop = asyncio.Event()

        async def _incr(root, summarizer, *, db, base_sha, head_sha, concurrency=8):
            calls["incr"] = (base_sha, head_sha)
            stop.set()

        monkeypatch.setattr(build_mod, "incremental_update", _incr)
        await worker.run(ctx, stop)

    asyncio.run(_run())
    assert calls["incr"] == ("OLD_SHA", "NEW_SHA")
    assert "fresh" not in calls


def test_idle_when_head_matches(monkeypatch):
    calls: dict[str, object] = {}

    async def _head(_root):
        return "SAME_SHA"

    def _latest(_db):
        return "SAME_SHA"

    async def _fresh(*a, **k):  # pragma: no cover
        calls["fresh"] = True

    async def _incr(*a, **k):  # pragma: no cover
        calls["incr"] = True

    monkeypatch.setattr(git_diff_mod, "head_commit", _head)
    monkeypatch.setattr(store_mod, "latest_map_sha", _latest)
    monkeypatch.setattr(build_mod, "fresh_build", _fresh)
    monkeypatch.setattr(build_mod, "incremental_update", _incr)

    worker = CodebaseMapWorker(interval_s=0.001)
    worker._summarizer = _SummarizerStub()
    ctx = _ctx()

    async def _run() -> None:
        stop = asyncio.Event()
        seen = {"ticks": 0}
        real_head = _head

        async def _head_then_stop(root):
            seen["ticks"] += 1
            stop.set()  # stop after this idle tick
            return await real_head(root)

        monkeypatch.setattr(git_diff_mod, "head_commit", _head_then_stop)
        await worker.run(ctx, stop)
        assert seen["ticks"] == 1

    asyncio.run(_run())
    assert "fresh" not in calls
    assert "incr" not in calls


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
