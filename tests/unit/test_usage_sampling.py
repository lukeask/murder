from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from murder.config import Config, CrowHandlerConfig, HarnessRoleConfig, ProjectConfig
from murder.llm.harnesses.base import HarnessAdapter
from murder.llm.harnesses.models import HarnessStartSpec, HarnessUsageStatus, HarnessUsageWindow
from murder.llm.harnesses.results import fail_result, ok_result
from murder.llm.harnesses.usage_sampling import (
    UsageSamplingContext,
    harness_kinds_to_sample,
    sample_harness_usages,
)
from murder.state.persistence.schema import init_db
from murder.state.persistence.usage import get_usage_probe_session_id, set_usage_probe_session_id


class _StubUsageAdapter(HarnessAdapter):
    kind = "codex"
    usage_collection_mode = "tmux_slash"
    _result = ok_result(
        HarnessUsageStatus(
            harness="codex",
            source="slash:/status",
            fetched_at="2026-06-04T00:00:00+00:00",
            windows=[HarnessUsageWindow(name="5h", percent_used=25.0)],
            raw={"session_id": "fresh-session-id"},
        )
    )

    def startup_cmd(self, cwd: Path) -> list[str]:
        del cwd
        cmd = ["stub-harness"]
        if self.resume_session_id:
            cmd += ["--resume", self.resume_session_id]
        return cmd

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


class _HttpUsageAdapter(HarnessAdapter):
    kind = "cursor"
    usage_collection_mode = "http"
    _result = ok_result(
        HarnessUsageStatus(
            harness="cursor",
            source="http:api",
            fetched_at="2026-06-04T00:00:00+00:00",
            windows=[HarnessUsageWindow(name="5h", percent_used=10.0)],
            raw={},
        )
    )

    def startup_cmd(self, cwd: Path) -> list[str]:
        del cwd
        return ["cursor-stub"]

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


def _mixed_pool_config() -> Config:
    role = HarnessRoleConfig(harness="codex", harnesses=["codex", "cursor"])
    return Config(
        project=ProjectConfig(name="repo"),
        collaborator=role,
        default_crow=role,
        crow_handler=CrowHandlerConfig(model="test-model"),
    )


async def _capture_pane(session: str, *, lines: int = 120) -> str:
    del session, lines
    return "idle"


def test_harness_kinds_to_sample_none_includes_all_usage_kinds() -> None:
    ctx = UsageSamplingContext(config=_mixed_pool_config(), repo_root=Path("/tmp"), db=None)
    monkeypatch_registry = {"codex": _StubUsageAdapter, "cursor": _HttpUsageAdapter}
    import murder.llm.harnesses.usage_sampling as usage_sampling_mod

    original_registry = usage_sampling_mod.REGISTRY
    usage_sampling_mod.REGISTRY = monkeypatch_registry  # type: ignore[assignment]
    try:
        kinds = harness_kinds_to_sample(ctx, modes=None)
    finally:
        usage_sampling_mod.REGISTRY = original_registry

    assert kinds == ["codex", "cursor"]


def test_harness_kinds_to_sample_http_filter_excludes_tmux_slash() -> None:
    ctx = UsageSamplingContext(config=_mixed_pool_config(), repo_root=Path("/tmp"), db=None)
    import murder.llm.harnesses.usage_sampling as usage_sampling_mod

    original_registry = usage_sampling_mod.REGISTRY
    usage_sampling_mod.REGISTRY = {"codex": _StubUsageAdapter, "cursor": _HttpUsageAdapter}  # type: ignore[assignment]
    try:
        kinds = harness_kinds_to_sample(ctx, modes={"http"})
    finally:
        usage_sampling_mod.REGISTRY = original_registry

    assert kinds == ["cursor"]


def test_sample_harness_usages_http_filter_skips_tmux_slash(monkeypatch, tmp_path) -> None:
    creates: list[tuple[str, Path, list[str]]] = []
    inserted: list[str] = []

    async def _kill_session(name: str) -> None:
        del name

    async def _create_session(name: str, cwd: Path, cmd: list[str]) -> None:
        creates.append((name, cwd, cmd))

    monkeypatch.setattr(
        "murder.llm.harnesses.usage_sampling.REGISTRY",
        {"codex": _StubUsageAdapter, "cursor": _HttpUsageAdapter},
    )
    monkeypatch.setattr(
        "murder.llm.harnesses.usage_sampling.get_harness",
        lambda kind, startup_model=None: (
            _HttpUsageAdapter(startup_model=startup_model)
            if kind == "cursor"
            else _StubUsageAdapter(startup_model=startup_model)
        ),
    )
    monkeypatch.setattr("murder.llm.harnesses.usage_sampling.tmux.kill_session", _kill_session)
    monkeypatch.setattr("murder.llm.harnesses.usage_sampling.tmux.create_session", _create_session)
    monkeypatch.setattr("murder.llm.harnesses.usage_sampling.tmux.capture_pane", _capture_pane)
    monkeypatch.setattr(
        "murder.llm.harnesses.usage_sampling.insert_harness_usage_snapshot",
        lambda db, status: inserted.append(status.harness),
    )

    ctx = UsageSamplingContext(config=_mixed_pool_config(), repo_root=tmp_path, db=_db())
    stored, failures = asyncio.run(sample_harness_usages(ctx, modes={"http"}))

    assert stored == 1
    assert failures == 0
    assert inserted == ["cursor"]
    assert creates == []


def test_sample_harness_usages_starts_fresh_session_and_cleans_up(monkeypatch, tmp_path) -> None:
    kills: list[str] = []
    creates: list[tuple[str, Path, list[str]]] = []
    inserted: list[HarnessUsageStatus] = []

    async def _kill_session(name: str) -> None:
        kills.append(name)

    async def _create_session(name: str, cwd: Path, cmd: list[str]) -> None:
        creates.append((name, cwd, cmd))

    monkeypatch.setattr("murder.llm.harnesses.usage_sampling.REGISTRY", {"codex": _StubUsageAdapter})
    monkeypatch.setattr(
        "murder.llm.harnesses.usage_sampling.get_harness",
        lambda kind, startup_model=None: _StubUsageAdapter(startup_model=startup_model),
    )
    monkeypatch.setattr("murder.llm.harnesses.usage_sampling.tmux.kill_session", _kill_session)
    monkeypatch.setattr("murder.llm.harnesses.usage_sampling.tmux.create_session", _create_session)
    monkeypatch.setattr("murder.llm.harnesses.usage_sampling.tmux.capture_pane", _capture_pane)
    monkeypatch.setattr(
        "murder.llm.harnesses.usage_sampling.insert_harness_usage_snapshot",
        lambda db, status: inserted.append(status),
    )

    db = _db()
    ctx = UsageSamplingContext(config=_config(), repo_root=tmp_path, db=db)
    stored, failures = asyncio.run(sample_harness_usages(ctx))

    assert stored == 1
    assert failures == 0
    assert len(creates) == 1
    assert creates[0][2] == ["stub-harness"]
    assert len(inserted) == 1
    assert kills == [creates[0][0], creates[0][0]]
    assert get_usage_probe_session_id(db, "codex") == "fresh-session-id"


def test_sample_harness_usages_resumes_cached_session(monkeypatch, tmp_path) -> None:
    creates: list[tuple[str, Path, list[str]]] = []

    async def _kill_session(name: str) -> None:
        del name

    async def _create_session(name: str, cwd: Path, cmd: list[str]) -> None:
        creates.append((name, cwd, cmd))

    monkeypatch.setattr("murder.llm.harnesses.usage_sampling.REGISTRY", {"codex": _StubUsageAdapter})
    monkeypatch.setattr(
        "murder.llm.harnesses.usage_sampling.get_harness",
        lambda kind, startup_model=None: _StubUsageAdapter(startup_model=startup_model),
    )
    monkeypatch.setattr("murder.llm.harnesses.usage_sampling.tmux.kill_session", _kill_session)
    monkeypatch.setattr("murder.llm.harnesses.usage_sampling.tmux.create_session", _create_session)
    monkeypatch.setattr("murder.llm.harnesses.usage_sampling.tmux.capture_pane", _capture_pane)
    monkeypatch.setattr(
        "murder.llm.harnesses.usage_sampling.insert_harness_usage_snapshot",
        lambda db, status: None,
    )

    db = _db()
    set_usage_probe_session_id(db, "codex", "cached-session-id")
    ctx = UsageSamplingContext(config=_config(), repo_root=tmp_path, db=db)
    stored, failures = asyncio.run(sample_harness_usages(ctx))

    assert stored == 1
    assert failures == 0
    assert creates[0][2] == ["stub-harness", "--resume", "cached-session-id"]


def test_sample_harness_usages_cleans_up_session_on_parse_failure(monkeypatch, tmp_path) -> None:
    kills: list[str] = []
    creates: list[str] = []

    class _FailingUsageAdapter(_StubUsageAdapter):
        _result = fail_result("no usage")

    async def _kill_session(name: str) -> None:
        kills.append(name)

    async def _create_session(name: str, cwd: Path, cmd: list[str]) -> None:
        del cwd, cmd
        creates.append(name)

    monkeypatch.setattr("murder.llm.harnesses.usage_sampling.REGISTRY", {"codex": _FailingUsageAdapter})
    monkeypatch.setattr(
        "murder.llm.harnesses.usage_sampling.get_harness",
        lambda kind, startup_model=None: _FailingUsageAdapter(startup_model=startup_model),
    )
    monkeypatch.setattr("murder.llm.harnesses.usage_sampling.tmux.kill_session", _kill_session)
    monkeypatch.setattr("murder.llm.harnesses.usage_sampling.tmux.create_session", _create_session)
    monkeypatch.setattr("murder.llm.harnesses.usage_sampling.tmux.capture_pane", _capture_pane)

    ctx = UsageSamplingContext(config=_config(), repo_root=tmp_path, db=_db())
    stored, failures = asyncio.run(sample_harness_usages(ctx))

    assert stored == 0
    assert failures == 1
    assert len(creates) == 1
    assert kills == [creates[0], creates[0]]


def test_sample_harness_usages_clears_invalid_cached_resume_and_retries(
    monkeypatch,
    tmp_path,
) -> None:
    from murder.llm.harnesses import usage_sampling

    calls: list[str | None] = []
    inserted: list[HarnessUsageStatus] = []

    class _Adapter(_StubUsageAdapter):
        def detects_invalid_resume(self, pane_text: str) -> bool:
            return "No saved session found" in pane_text

    class _FakeSession:
        session = "usage-session"
        adapter = _Adapter()

        async def collect_usage_status(self):
            return _StubUsageAdapter._result

    async def _start(ctx, kind, startup_model, *, resume_session_id=None):
        del ctx, kind, startup_model
        calls.append(resume_session_id)
        return None if resume_session_id else _FakeSession()

    async def _capture_pane_invalid(session: str, *, lines: int = 120) -> str:
        del session, lines
        return "ERROR: No saved session found with ID cached-session-id."

    async def _kill_session(name: str) -> None:
        del name

    monkeypatch.setattr("murder.llm.harnesses.usage_sampling.REGISTRY", {"codex": _Adapter})
    monkeypatch.setattr(
        "murder.llm.harnesses.usage_sampling.get_harness",
        lambda kind, startup_model=None: _Adapter(startup_model=startup_model),
    )
    monkeypatch.setattr(usage_sampling, "_start_tmux_slash_session", _start)
    monkeypatch.setattr("murder.llm.harnesses.usage_sampling.tmux.capture_pane", _capture_pane_invalid)
    monkeypatch.setattr("murder.llm.harnesses.usage_sampling.tmux.kill_session", _kill_session)
    monkeypatch.setattr(
        "murder.llm.harnesses.usage_sampling.insert_harness_usage_snapshot",
        lambda db, status: inserted.append(status),
    )

    db = _db()
    set_usage_probe_session_id(db, "codex", "cached-session-id")
    ctx = UsageSamplingContext(config=_config(), repo_root=tmp_path, db=db)
    stored, failures = asyncio.run(sample_harness_usages(ctx))

    assert calls == ["cached-session-id", None]
    assert stored == 1
    assert failures == 0
    assert len(inserted) == 1
    assert get_usage_probe_session_id(db, "codex") == "fresh-session-id"
