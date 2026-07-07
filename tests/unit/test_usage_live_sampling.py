from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from murder.config import Config, CrowHandlerConfig, HarnessRoleConfig, ProjectConfig
from murder.llm.harnesses.base import HarnessAdapter, HarnessSession
from murder.llm.harnesses.models import HarnessStartSpec, HarnessUsageStatus, HarnessUsageWindow
from murder.llm.harnesses.results import fail_result, ok_result
from murder.llm.harnesses.usage_sampling import (
    LiveSessionUsageResult,
    UsageSamplingContext,
    sample_live_session_usage,
)
from murder.state.persistence.schema import init_db


class _StubUsageAdapter(HarnessAdapter):
    kind = "codex"
    usage_collection_mode = "tmux_slash"
    _result = ok_result(
        HarnessUsageStatus(
            harness="codex",
            source="slash:/status",
            fetched_at="2026-06-04T00:00:00+00:00",
            windows=[HarnessUsageWindow(name="5h", percent_used=25.0)],
            raw={"session_id": "live-session-id"},
        )
    )

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

    async def initialize_defaults(self, session: str, spec: HarnessStartSpec):  # type: ignore[override]
        del session, spec
        return ok_result()

    async def collect_usage_status(self, session: str):
        del session
        return self._result

    def extract_last_message(self, pane_text: str) -> str | None:
        del pane_text
        return None


class _HttpOnlyAdapter(_StubUsageAdapter):
    kind = "cursor"
    usage_collection_mode = "http"


class _FakeProducer:
    def __init__(self, last_state: str | None) -> None:
        self.last_state = last_state


class _FakeHarnessSession:
    def __init__(self, adapter: HarnessAdapter, session: str = "crow-1") -> None:
        self.adapter = adapter
        self.session = session
        self.repo_root = Path("/tmp")
        self.collect_calls = 0

    async def collect_usage_status(self):
        self.collect_calls += 1
        return await self.adapter.collect_usage_status(self.session)

    async def wait_idle(self, timeout_s: float = 30.0):
        del timeout_s
        return ok_result()


class _FakeAgent:
    def __init__(
        self,
        *,
        harness: HarnessAdapter,
        harness_session: _FakeHarnessSession,
        producer: _FakeProducer | None = None,
    ) -> None:
        self.harness = harness
        self.harness_session = harness_session
        self._producer = producer
        self.usage_capture_in_progress = False


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


def test_live_sample_inserts_snapshot_with_trigger(monkeypatch, tmp_path) -> None:
    sent: list[tuple[str, str, bool, bool]] = []
    inserted: list[HarnessUsageStatus] = []

    async def _send_keys(session: str, keys: str, *, literal: bool, enter: bool) -> None:
        sent.append((session, keys, literal, enter))

    async def _interrupt_generation(self, session: str) -> None:
        del self
        sent.append((session, "Escape", False, False))

    monkeypatch.setattr(
        "murder.llm.harnesses.usage_sampling.tmux.send_keys",
        _send_keys,
    )
    monkeypatch.setattr(
        _StubUsageAdapter,
        "interrupt_generation",
        _interrupt_generation,
    )
    monkeypatch.setattr(
        "murder.llm.harnesses.usage_sampling.REGISTRY",
        {"codex": _StubUsageAdapter},
    )
    monkeypatch.setattr(
        "murder.llm.harnesses.usage_sampling.insert_harness_usage_snapshot",
        lambda db, status: inserted.append(status),
    )

    adapter = _StubUsageAdapter()
    hs = _FakeHarnessSession(adapter, session="crow-ticket-1")
    agent = _FakeAgent(
        harness=adapter,
        harness_session=hs,
        producer=_FakeProducer("awaiting_input"),
    )
    db = _db()
    ctx = UsageSamplingContext(config=_config(), repo_root=tmp_path, db=db)

    result = asyncio.run(sample_live_session_usage(agent, ctx, "agent_startup"))

    assert result == LiveSessionUsageResult(outcome="stored")
    assert hs.collect_calls == 1
    assert len(inserted) == 1
    assert inserted[0].raw.get("trigger") == "agent_startup"
    assert any(keys == "Escape" for _, keys, _, _ in sent)


def test_live_sample_skips_when_not_idle(monkeypatch, tmp_path) -> None:
    sent: list[tuple[str, str]] = []

    async def _send_keys(session: str, keys: str, *, literal: bool, enter: bool) -> None:
        del literal, enter
        sent.append((session, keys))

    monkeypatch.setattr(
        "murder.llm.harnesses.usage_sampling.tmux.send_keys",
        _send_keys,
    )
    monkeypatch.setattr(
        "murder.llm.harnesses.usage_sampling.REGISTRY",
        {"codex": _StubUsageAdapter},
    )
    monkeypatch.setattr(
        "murder.llm.harnesses.usage_sampling.insert_harness_usage_snapshot",
        lambda db, status: (_ for _ in ()).throw(AssertionError("should not insert")),
    )

    adapter = _StubUsageAdapter()
    hs = _FakeHarnessSession(adapter)
    agent = _FakeAgent(
        harness=adapter,
        harness_session=hs,
        producer=_FakeProducer("working"),
    )
    ctx = UsageSamplingContext(config=_config(), repo_root=tmp_path, db=_db())

    result = asyncio.run(sample_live_session_usage(agent, ctx, "agent_shutdown"))

    assert result == LiveSessionUsageResult(outcome="skipped", reason="not_idle")
    assert hs.collect_calls == 0
    assert sent == []


def test_live_sample_noop_for_non_tmux_slash_harness(tmp_path) -> None:
    adapter = _HttpOnlyAdapter()
    hs = _FakeHarnessSession(adapter)
    agent = _FakeAgent(
        harness=adapter,
        harness_session=hs,
        producer=_FakeProducer("awaiting_input"),
    )
    ctx = UsageSamplingContext(config=_config(), repo_root=tmp_path, db=_db())

    result = asyncio.run(sample_live_session_usage(agent, ctx, "agent_startup"))

    assert result == LiveSessionUsageResult(outcome="noop", reason="unsupported_harness")
    assert hs.collect_calls == 0


def test_live_sample_parse_failure_does_not_raise(monkeypatch, tmp_path) -> None:
    class _FailingAdapter(_StubUsageAdapter):
        _result = fail_result("no usage")

    async def _interrupt_generation(self, session: str) -> None:
        del self, session

    monkeypatch.setattr(
        _FailingAdapter,
        "interrupt_generation",
        _interrupt_generation,
    )
    monkeypatch.setattr(
        "murder.llm.harnesses.usage_sampling.REGISTRY",
        {"codex": _FailingAdapter},
    )
    monkeypatch.setattr(
        "murder.llm.harnesses.usage_sampling.insert_harness_usage_snapshot",
        lambda db, status: (_ for _ in ()).throw(AssertionError("should not insert")),
    )

    adapter = _FailingAdapter()
    hs = _FakeHarnessSession(adapter)
    agent = _FakeAgent(
        harness=adapter,
        harness_session=hs,
        producer=_FakeProducer("awaiting_input"),
    )
    ctx = UsageSamplingContext(config=_config(), repo_root=tmp_path, db=_db())

    result = asyncio.run(sample_live_session_usage(agent, ctx, "agent_shutdown"))

    assert result.outcome == "failed"
    assert hs.collect_calls == 1
