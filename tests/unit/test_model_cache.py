"""Tests for the startup model-discovery cache (C9 / B8).

Covers the accessor's classvar fallback, cache population from discovery, and
the graceful-timeout / failure paths that must never block startup.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from murder.llm.harnesses import REGISTRY, capabilities_for, model_cache
from murder.llm.harnesses.model_cache import (
    clear_model_cache,
    enabled_harnesses,
    get_available_models,
    populate_model_cache,
    set_discovered_models,
)
from murder.llm.harnesses.results import SimpleResult, fail_result, ok_result


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_model_cache()
    yield
    clear_model_cache()


def test_accessor_falls_back_to_classvar_when_empty():
    fallback = list(REGISTRY["claude_code"].available_startup_models)
    assert fallback  # sanity: claude_code ships a non-empty classvar
    assert get_available_models("claude_code") == fallback


def test_accessor_returns_discovered_over_classvar():
    discovered = [("disco-model", "Disco Model")]
    set_discovered_models("claude_code", discovered)
    assert get_available_models("claude_code") == discovered


def test_set_discovered_ignores_empty():
    set_discovered_models("claude_code", [])
    # still falls back to classvar
    assert get_available_models("claude_code") == list(
        REGISTRY["claude_code"].available_startup_models
    )


def test_accessor_unknown_harness_returns_empty():
    assert get_available_models("not_a_harness") == []


def test_accessor_returns_fresh_list_each_call():
    set_discovered_models("codex", [("m", "M")])
    a = get_available_models("codex")
    a.append(("mutated", "x"))
    assert get_available_models("codex") == [("m", "M")]


def test_enabled_harnesses_are_discovery_capable():
    enabled = enabled_harnesses()
    assert enabled  # at least one capable harness
    for kind in enabled:
        assert capabilities_for(kind).model_discovery


def test_populate_caches_discovered_models(monkeypatch):
    enabled = enabled_harnesses()

    async def fake_discover(kind: str, repo_root: Path, **_kw) -> SimpleResult:
        return ok_result([(f"{kind}-x", f"{kind} X")])

    monkeypatch.setattr(model_cache, "discover_harness_models", fake_discover)
    asyncio.run(populate_model_cache(Path("/repo")))

    for kind in enabled:
        assert get_available_models(kind) == [(f"{kind}-x", f"{kind} X")]


def test_populate_failure_leaves_fallback(monkeypatch):
    async def fake_discover(kind: str, repo_root: Path, **_kw) -> SimpleResult:
        return fail_result("nope")

    monkeypatch.setattr(model_cache, "discover_harness_models", fake_discover)
    asyncio.run(populate_model_cache(Path("/repo")))

    # nothing cached -> classvar fallback intact
    assert get_available_models("claude_code") == list(
        REGISTRY["claude_code"].available_startup_models
    )


def test_populate_timeout_is_graceful(monkeypatch):
    """A hung probe must not block populate or poison other harnesses."""

    async def hung_discover(kind: str, repo_root: Path, **_kw) -> SimpleResult:
        if kind == "claude_code":
            await asyncio.Event().wait()  # never resolves
        return ok_result([(f"{kind}-ok", f"{kind} OK")])

    monkeypatch.setattr(model_cache, "discover_harness_models", hung_discover)
    # tiny timeout so the test is fast; real sleep is patched to noop in conftest
    # but asyncio.timeout uses the loop clock, so the hung Event wins the race.
    asyncio.run(populate_model_cache(Path("/repo"), timeout_s=0.05))

    # hung harness fell back; others still populated
    assert get_available_models("claude_code") == list(
        REGISTRY["claude_code"].available_startup_models
    )
    for kind in enabled_harnesses():
        if kind != "claude_code":
            assert get_available_models(kind) == [(f"{kind}-ok", f"{kind} OK")]


def test_populate_swallows_raises(monkeypatch):
    async def raising_discover(kind: str, repo_root: Path, **_kw) -> SimpleResult:
        raise RuntimeError("boom")

    monkeypatch.setattr(model_cache, "discover_harness_models", raising_discover)
    # must not raise
    asyncio.run(populate_model_cache(Path("/repo")))
    assert get_available_models("codex") == list(
        REGISTRY["codex"].available_startup_models
    )
