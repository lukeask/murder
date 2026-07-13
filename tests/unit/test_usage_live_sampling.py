"""Live usage sampling delegates only to verified control capabilities."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

from murder.config import Config, CrowHandlerConfig, HarnessRoleConfig, ProjectConfig
from murder.llm.harnesses.base import HarnessAdapter
from murder.llm.harnesses.models import HarnessUsageStatus, HarnessUsageWindow
from murder.llm.harnesses.usage_sampling import (
    LiveSessionUsageResult,
    UsageSamplingContext,
    sample_live_session_usage,
)
from murder.state.persistence.schema import init_db


class _StubUsageAdapter(HarnessAdapter):
    kind = "codex"
    usage_collection_mode = "tmux_slash"

    def startup_cmd(self, cwd: Path) -> list[str]:
        del cwd
        return ["stub-harness"]

    def is_ready(self, pane_text: str) -> bool:
        del pane_text
        return True

    def is_idle(self, pane_text: str) -> bool:
        del pane_text
        return True

    def is_busy(self, pane_text: str) -> bool:
        del pane_text
        return False

    def extract_last_message(self, pane_text: str) -> str | None:
        del pane_text
        return None


class _HttpOnlyAdapter(_StubUsageAdapter):
    kind = "cursor"
    usage_collection_mode = "http"


class _VerifiedUsageControl:
    def __init__(self, status: HarnessUsageStatus | None) -> None:
        self.status = status
        self.triggers: list[str] = []

    async def collect_usage(self, *, trigger: str) -> HarnessUsageStatus | None:
        self.triggers.append(trigger)
        return self.status


class _FakeAgent:
    def __init__(self, harness: HarnessAdapter, control: object | None) -> None:
        self.harness = harness
        self.verified_harness_control = control


def _config() -> Config:
    role = HarnessRoleConfig(harness="codex")
    return Config(
        project=ProjectConfig(name="repo"),
        collaborator=role,
        default_crow=role,
        crow_handler=CrowHandlerConfig(model="test-model"),
    )


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def _status() -> HarnessUsageStatus:
    return HarnessUsageStatus(
        harness="codex",
        source="slash:/status",
        fetched_at="2026-06-04T00:00:00+00:00",
        windows=[HarnessUsageWindow(name="5h", percent_used=25.0)],
        raw={"session_id": "live-session-id"},
    )


def test_live_sample_persists_only_verified_usage_result(tmp_path: Path) -> None:
    control = _VerifiedUsageControl(_status())
    agent = _FakeAgent(_StubUsageAdapter(), control)
    db = _db()
    ctx = UsageSamplingContext(config=_config(), repo_root=tmp_path, db=db)

    result = asyncio.run(sample_live_session_usage(agent, ctx, "agent_startup"))

    assert result == LiveSessionUsageResult(outcome="stored")
    assert control.triggers == ["agent_startup"]
    row = db.execute("SELECT status_json FROM harness_usage_snapshots").fetchone()
    assert row is not None
    assert json.loads(row["status_json"])["raw"]["trigger"] == "agent_startup"


def test_live_sample_skips_without_a_verified_usage_capability(tmp_path: Path) -> None:
    agent = _FakeAgent(_StubUsageAdapter(), None)
    ctx = UsageSamplingContext(config=_config(), repo_root=tmp_path, db=_db())

    result = asyncio.run(sample_live_session_usage(agent, ctx, "agent_shutdown"))

    assert result == LiveSessionUsageResult(
        outcome="skipped", reason="verified_usage_unavailable"
    )


def test_live_sample_noops_for_side_channel_harness(tmp_path: Path) -> None:
    control = _VerifiedUsageControl(_status())
    agent = _FakeAgent(_HttpOnlyAdapter(), control)
    ctx = UsageSamplingContext(config=_config(), repo_root=tmp_path, db=_db())

    result = asyncio.run(sample_live_session_usage(agent, ctx, "agent_startup"))

    assert result == LiveSessionUsageResult(outcome="noop", reason="unsupported_harness")
    assert control.triggers == []


def test_live_sample_reports_failed_verified_usage_request(tmp_path: Path) -> None:
    control = _VerifiedUsageControl(None)
    agent = _FakeAgent(_StubUsageAdapter(), control)
    ctx = UsageSamplingContext(config=_config(), repo_root=tmp_path, db=_db())

    result = asyncio.run(sample_live_session_usage(agent, ctx, "agent_shutdown"))

    assert result == LiveSessionUsageResult(
        outcome="failed", reason="verified_usage_unavailable"
    )
    assert control.triggers == ["agent_shutdown"]
