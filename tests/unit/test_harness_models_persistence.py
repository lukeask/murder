"""Tests for harness_models DB persistence and the state.harness_models_snapshot RPC.

Covers:
- Round-trip upsert/read of harness_models rows.
- Snapshot shape (exact contract verification including "as_of" and "models" keys).
- Startup persistence: refresh_and_persist_harness_models writes to DB.
- Missing-table guard (old DB without harness_models).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from murder.app.service.host import ServiceHost
from murder.app.service.read_model import ServiceReadModel
from murder.config import Config, CrowHandlerConfig, HarnessRoleConfig, ProjectConfig
from murder.llm.harnesses import model_cache as mc
from murder.llm.harnesses.model_cache import (
    clear_model_cache,
    refresh_and_persist_harness_models,
)
from murder.llm.harnesses.results import ok_result, fail_result
from murder.state.persistence.harness_models import (
    get_all_harness_models,
    upsert_harness_models,
)
from murder.state.persistence.schema import db_path_for, get_db, init_db
from murder.state.storage.paths import db_path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_model_cache():
    clear_model_cache()
    yield
    clear_model_cache()


@pytest.fixture
def db_conn(repo_root):
    conn = get_db(db_path(repo_root))
    init_db(conn)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# 1. Round-trip upsert / read
# ---------------------------------------------------------------------------


def test_upsert_and_read_roundtrip(db_conn):
    models = [{"id": "m1", "label": "Model 1"}, {"id": "m2", "label": "Model 2"}]
    upsert_harness_models(db_conn, harness="claude_code", models=models, fetched_at="2026-06-09T12:00:00")
    db_conn.commit()

    rows = get_all_harness_models(db_conn)
    assert len(rows) == 1
    row = rows[0]
    assert row["harness"] == "claude_code"
    assert row["fetched_at"] == "2026-06-09T12:00:00"
    assert row["models"] == models
    assert row["discovery_error"] is None


def test_upsert_replaces_existing_row(db_conn):
    upsert_harness_models(
        db_conn,
        harness="claude_code",
        models=[{"id": "old", "label": "Old"}],
        fetched_at="2026-06-09T10:00:00",
    )
    db_conn.commit()
    upsert_harness_models(
        db_conn,
        harness="claude_code",
        models=[{"id": "new", "label": "New"}],
        fetched_at="2026-06-09T11:00:00",
    )
    db_conn.commit()

    rows = get_all_harness_models(db_conn)
    assert len(rows) == 1
    assert rows[0]["models"] == [{"id": "new", "label": "New"}]
    assert rows[0]["fetched_at"] == "2026-06-09T11:00:00"


def test_upsert_persists_discovery_error(db_conn):
    upsert_harness_models(
        db_conn,
        harness="codex",
        models=[],
        discovery_error="timeout",
    )
    db_conn.commit()

    rows = get_all_harness_models(db_conn)
    assert len(rows) == 1
    assert rows[0]["discovery_error"] == "timeout"
    assert rows[0]["models"] == []


def test_upsert_multiple_harnesses(db_conn):
    upsert_harness_models(db_conn, harness="claude_code", models=[{"id": "a", "label": "A"}])
    upsert_harness_models(db_conn, harness="codex", models=[{"id": "b", "label": "B"}])
    db_conn.commit()

    rows = get_all_harness_models(db_conn)
    harnesses = {r["harness"] for r in rows}
    assert harnesses == {"claude_code", "codex"}


# ---------------------------------------------------------------------------
# 2. Snapshot shape (exact contract)
# ---------------------------------------------------------------------------


def test_snapshot_shape_empty_table(repo_root, db_conn):
    # No rows → models={}, as_of=null
    snapshot = ServiceReadModel(db_path(repo_root)).get_harness_models_snapshot()
    assert snapshot == {"models": {}, "as_of": None}


def test_snapshot_shape_with_rows(repo_root, db_conn):
    models = [{"id": "claude-sonnet-4-6", "label": "Sonnet 4.6"}]
    upsert_harness_models(
        db_conn,
        harness="claude_code",
        models=models,
        fetched_at="2026-06-09T15:30:00",
    )
    db_conn.commit()
    db_conn.close()

    snapshot = ServiceReadModel(db_path(repo_root)).get_harness_models_snapshot()
    assert snapshot["as_of"] == "2026-06-09T15:30:00"
    assert "claude_code" in snapshot["models"]
    assert snapshot["models"]["claude_code"] == models


def test_host_registers_live_harness_models_snapshot_rpc(repo_root):
    """The live bus handler must be registered, not only modeled by FakeBusClient."""
    host = ServiceHost(
        config=Config(
            project=ProjectConfig(name="repo"),
            collaborator=HarnessRoleConfig(harness="codex"),
            default_crow=HarnessRoleConfig(harness="codex"),
            crow_handler=CrowHandlerConfig(model="test-model"),
        ),
        repo_root=repo_root,
    )
    host.read_model = ServiceReadModel(db_path(repo_root))
    host.register_default_rpc_handlers()

    assert "state.harness_models_snapshot" in host._rpc_handlers


def test_snapshot_as_of_is_max_fetched_at(repo_root, db_conn):
    """as_of must be the maximum fetched_at across all rows."""
    upsert_harness_models(
        db_conn,
        harness="claude_code",
        models=[{"id": "x", "label": "X"}],
        fetched_at="2026-06-09T10:00:00",
    )
    upsert_harness_models(
        db_conn,
        harness="codex",
        models=[{"id": "y", "label": "Y"}],
        fetched_at="2026-06-09T12:00:00",
    )
    db_conn.commit()
    db_conn.close()

    snapshot = ServiceReadModel(db_path(repo_root)).get_harness_models_snapshot()
    # as_of is the max — "2026-06-09T12:00:00"
    assert snapshot["as_of"] == "2026-06-09T12:00:00"
    assert set(snapshot["models"].keys()) == {"claude_code", "codex"}


def test_snapshot_missing_table_guard(repo_root):
    """get_harness_models_snapshot returns safe default on a DB without the table."""
    # Create the .murder dir but open a bare DB (no init_db → no harness_models table)
    import sqlite3

    db_file = db_path(repo_root)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_file))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.close()

    snapshot = ServiceReadModel(db_path(repo_root)).get_harness_models_snapshot()
    assert snapshot == {"models": {}, "as_of": None}


# ---------------------------------------------------------------------------
# 3. refresh_and_persist_harness_models writes to DB
# ---------------------------------------------------------------------------


def test_refresh_persists_discovered_models(repo_root, db_conn, monkeypatch):
    """Successful discovery should appear in the DB after refresh."""

    async def fake_discover(kind: str, repo_root: Path, **_kw):
        return ok_result([(f"{kind}-model", f"{kind.title()} Model")])

    monkeypatch.setattr(mc, "discover_harness_models", fake_discover)

    asyncio.run(refresh_and_persist_harness_models(repo_root, db_conn))

    rows = get_all_harness_models(db_conn)
    harnesses_persisted = {r["harness"] for r in rows}
    # At least one harness should have been persisted
    assert len(harnesses_persisted) > 0
    for row in rows:
        # Success rows: no error, model list non-empty
        assert row["discovery_error"] is None
        assert len(row["models"]) > 0
        assert row["models"][0]["id"].endswith("-model")
        assert row["models"][0]["label"].endswith(" Model")


def test_refresh_persists_failure_rows(repo_root, db_conn, monkeypatch):
    """A failed probe should store the error in discovery_error."""
    from murder.llm.harnesses.model_cache import enabled_harnesses

    enabled = enabled_harnesses()
    assert enabled, "need at least one enabled harness"

    async def fake_discover(kind: str, repo_root: Path, **_kw):
        return fail_result("probe failed")

    monkeypatch.setattr(mc, "discover_harness_models", fake_discover)

    asyncio.run(refresh_and_persist_harness_models(repo_root, db_conn))

    rows = get_all_harness_models(db_conn)
    assert len(rows) > 0
    for row in rows:
        assert row["discovery_error"] == "probe failed"


def test_refresh_no_db_does_not_raise(repo_root, monkeypatch):
    """refresh_and_persist_harness_models with db=None should not raise."""

    async def fake_discover(kind: str, repo_root: Path, **_kw):
        return ok_result([("x", "X")])

    monkeypatch.setattr(mc, "discover_harness_models", fake_discover)

    # Should not raise
    asyncio.run(refresh_and_persist_harness_models(repo_root, db=None))
    # Cache should be populated
    from murder.llm.harnesses.model_cache import get_available_models, enabled_harnesses
    for kind in enabled_harnesses():
        assert get_available_models(kind) == [("x", "X")]


def test_refresh_and_snapshot_end_to_end(repo_root, db_conn, monkeypatch):
    """Full round-trip: refresh writes DB, snapshot reads correct shape."""

    async def fake_discover(kind: str, repo_root: Path, **_kw):
        return ok_result([(f"{kind}-id", f"{kind} Label")])

    monkeypatch.setattr(mc, "discover_harness_models", fake_discover)

    asyncio.run(refresh_and_persist_harness_models(repo_root, db_conn))
    db_conn.commit()
    db_conn.close()

    snapshot = ServiceReadModel(db_path(repo_root)).get_harness_models_snapshot()
    assert snapshot["as_of"] is not None
    assert isinstance(snapshot["models"], dict)
    assert len(snapshot["models"]) > 0
    # Validate shape of inner model items
    for harness_kind, model_list in snapshot["models"].items():
        assert isinstance(harness_kind, str)
        assert isinstance(model_list, list)
        for item in model_list:
            assert "id" in item
            assert "label" in item
