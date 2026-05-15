"""Harness usage sampling helpers."""

import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from murder.config import HarnessRoleConfig
from murder.harnesses.models import HarnessUsageStatus, HarnessUsageWindow
from murder.harnesses.results import fail_result, ok_result
from murder.harnesses.usage_sampling import (
    _ensure_tmux_slash_session,
    harness_kinds_to_sample,
    harness_kinds_with_usage_collection,
    insert_harness_usage_snapshot,
    sample_harness_usages_for_config,
)


def _rt(*, crow: HarnessRoleConfig, collab_harness: str) -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(
            default_crow=crow,
            collaborator=SimpleNamespace(harness=collab_harness),
        )
    )


def test_harness_kinds_with_usage_collection_excludes_pi() -> None:
    cfg = HarnessRoleConfig(harness="cursor", harnesses=["cursor", "pi", "codex"])
    assert harness_kinds_with_usage_collection(cfg) == ["cursor", "codex"]


def test_harness_kinds_to_sample_adds_collaborator_harness() -> None:
    rt = _rt(crow=HarnessRoleConfig(harness="cursor"), collab_harness="claude_code")
    assert harness_kinds_to_sample(rt) == ["cursor", "claude_code"]


def test_harness_kinds_to_sample_dedupes_and_skips_pi_collaborator() -> None:
    rt = _rt(crow=HarnessRoleConfig(harness="codex"), collab_harness="codex")
    assert harness_kinds_to_sample(rt) == ["codex"]
    rt = _rt(crow=HarnessRoleConfig(harness="cursor"), collab_harness="pi")
    assert harness_kinds_to_sample(rt) == ["cursor"]


def test_harness_kinds_single_harness_pool() -> None:
    cfg = HarnessRoleConfig(harness="pi")
    assert harness_kinds_with_usage_collection(cfg) == []


def test_insert_harness_usage_snapshot_roundtrip(tmp_path) -> None:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        """
        CREATE TABLE harness_usage_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            harness TEXT NOT NULL,
            source TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            status_json TEXT NOT NULL
        )
        """
    )
    status = HarnessUsageStatus(
        harness="codex",
        source="slash:/status",
        fetched_at="2026-01-01T00:00:00Z",
        windows=[HarnessUsageWindow(name="plan", percent_used=12.0)],
    )
    insert_harness_usage_snapshot(db, status)
    row = db.execute("SELECT harness, source, status_json FROM harness_usage_snapshots").fetchone()
    assert row["harness"] == "codex"
    assert row["source"] == "slash:/status"
    assert "windows" in row["status_json"]


@pytest.mark.asyncio
async def test_sample_harness_usages_http_only(monkeypatch, tmp_path) -> None:
    rt = MagicMock()
    rt.db = sqlite3.connect(":memory:")
    rt.db.row_factory = sqlite3.Row
    rt.db.execute(
        """
        CREATE TABLE harness_usage_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            harness TEXT NOT NULL,
            source TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            status_json TEXT NOT NULL
        )
        """
    )
    rt.repo_root = tmp_path
    rt.config.default_crow = HarnessRoleConfig(harness="cursor")

    status = HarnessUsageStatus(
        harness="cursor",
        source="api",
        fetched_at="2026-01-01T00:00:00Z",
        windows=[],
    )

    adapter = MagicMock()
    adapter.collect_usage_status = AsyncMock(return_value=ok_result(status))
    monkeypatch.setattr(
        "murder.harnesses.usage_sampling.get_harness",
        lambda kind, startup_model=None: adapter,
    )

    stored, failures = await sample_harness_usages_for_config(rt)
    assert stored == 1
    assert failures == 0
    n = rt.db.execute("SELECT COUNT(*) AS c FROM harness_usage_snapshots").fetchone()["c"]
    assert n == 1


@pytest.mark.asyncio
async def test_ensure_tmux_slash_session_restarts_stale_existing_session(
    monkeypatch, tmp_path
) -> None:
    rt = SimpleNamespace(
        repo_root=tmp_path,
        config=SimpleNamespace(
            project=SimpleNamespace(name="proj"),
            runtime=SimpleNamespace(session_name_template="murder_{project}_{role}{suffix}"),
        ),
    )
    stale = MagicMock()
    stale.wait_ready = AsyncMock(return_value=fail_result("stale"))
    stale.wait_idle = AsyncMock(return_value=ok_result())
    fresh = MagicMock()
    fresh.start = AsyncMock(return_value=ok_result())

    adapter = MagicMock()
    adapter.attach = MagicMock(side_effect=[stale, fresh])
    monkeypatch.setattr(
        "murder.harnesses.usage_sampling.get_harness",
        lambda kind, startup_model=None: adapter,
    )
    monkeypatch.setattr(
        "murder.harnesses.usage_sampling.tmux.session_exists",
        AsyncMock(return_value=True),
    )
    kill_session = AsyncMock()
    monkeypatch.setattr("murder.harnesses.usage_sampling.tmux.kill_session", kill_session)

    hs = await _ensure_tmux_slash_session(rt, "codex", "gpt-5.5")
    assert hs is fresh
    stale.wait_ready.assert_awaited_once()
    stale.wait_idle.assert_not_awaited()
    kill_session.assert_awaited_once_with("murder_proj_usage_codex")
    fresh.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_sample_harness_usages_tmux_failure_resets_session(monkeypatch, tmp_path) -> None:
    rt = MagicMock()
    rt.db = sqlite3.connect(":memory:")
    rt.db.row_factory = sqlite3.Row
    rt.db.execute(
        """
        CREATE TABLE harness_usage_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            harness TEXT NOT NULL,
            source TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            status_json TEXT NOT NULL
        )
        """
    )
    rt.repo_root = tmp_path
    rt.config.default_crow = HarnessRoleConfig(harness="codex")
    rt.config.collaborator = SimpleNamespace(harness="pi")

    hs = MagicMock()
    hs.session = "murder_proj_usage_codex"
    hs.collect_usage_status = AsyncMock(return_value=fail_result("no usage data"))
    monkeypatch.setattr(
        "murder.harnesses.usage_sampling._ensure_tmux_slash_session",
        AsyncMock(return_value=hs),
    )
    kill_session = AsyncMock()
    monkeypatch.setattr("murder.harnesses.usage_sampling.tmux.kill_session", kill_session)

    stored, failures = await sample_harness_usages_for_config(rt)

    assert stored == 0
    assert failures == 1
    kill_session.assert_awaited_once_with("murder_proj_usage_codex")
