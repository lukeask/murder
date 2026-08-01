"""Live model-catalog cache and last-successful persistence contracts."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest

from murder.llm.harness_control.runtime.live_model_probe import LiveModelProbeResult
from murder.llm.harnesses import REGISTRY
from murder.llm.harnesses.model_cache import (
    CATALOG_ADVISORY,
    clear_model_cache,
    configured_harnesses,
    get_available_models,
    refresh_and_persist_harness_models,
)
from murder.state.persistence.harness_models import get_all_harness_models, upsert_harness_models
from tests.support.database import open_test_repo_db


def test_accessor_returns_configured_catalog_and_fresh_list() -> None:
    expected = list(REGISTRY["claude_code"].available_startup_models)
    models = get_available_models("claude_code")
    assert models == expected
    models.append(("mutated", "Mutated"))
    assert get_available_models("claude_code") == expected


def test_catalog_covers_all_registered_harnesses_without_capability_probe() -> None:
    assert configured_harnesses() == sorted(REGISTRY)
    assert get_available_models("not_a_harness") == []
    assert get_available_models("codex") == []


def test_failed_live_refresh_preserves_the_previously_persisted_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_probes(monkeypatch)
    db = open_test_repo_db(tmp_path / "murder.db")

    upsert_harness_models(
        db,
        harness="codex",
        models=[{"id": "last-good", "label": "Last Good"}],
        fetched_at="2026-01-01T00:00:00+00:00",
    )
    db.conn.commit()
    asyncio.run(refresh_and_persist_harness_models(tmp_path, db))

    rows = get_all_harness_models(db)
    assert {row["harness"] for row in rows} == {"claude_code", "codex", "pi"}
    assert (
        next(row for row in rows if row["harness"] == "claude_code")["discovery_error"]
        == CATALOG_ADVISORY
    )
    codex = next(row for row in rows if row["harness"] == "codex")
    assert codex["models"] == [{"id": "last-good", "label": "Last Good"}]
    assert codex["fetched_at"] == "2026-01-01T00:00:00+00:00"
    assert next(row for row in rows if row["harness"] == "claude_code")["models"] == [
        {"id": model_id, "label": label}
        for model_id, label in REGISTRY["claude_code"].available_startup_models
    ]


def test_refresh_caches_and_persists_live_codex_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def probe(kind: str, _root: Path, *, timeout_s: float) -> LiveModelProbeResult:
        del timeout_s
        if kind == "codex":
            return LiveModelProbeResult(True, (("gpt-current", "GPT Current"),))
        return LiveModelProbeResult(False, (), f"{kind} unavailable")

    monkeypatch.setattr("murder.llm.harnesses.model_cache.probe_live_models", probe)
    db = open_test_repo_db(tmp_path / "murder.db")

    asyncio.run(refresh_and_persist_harness_models(tmp_path, db))

    assert get_available_models("codex") == [("gpt-current", "GPT Current")]
    row = next(row for row in get_all_harness_models(db) if row["harness"] == "codex")
    assert row["models"] == [{"id": "gpt-current", "label": "GPT Current"}]
    assert row["discovery_error"] is None


@pytest.fixture(autouse=True)
def _isolated_cache() -> Iterator[None]:
    clear_model_cache()
    yield
    clear_model_cache()


def _stub_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def probe(kind: str, _root: Path, *, timeout_s: float) -> LiveModelProbeResult:
        del timeout_s
        return LiveModelProbeResult(False, (), f"{kind} unavailable")

    monkeypatch.setattr("murder.llm.harnesses.model_cache.probe_live_models", probe)
