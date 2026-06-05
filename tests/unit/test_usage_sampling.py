from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from murder.config import Config, CrowHandlerConfig, HarnessRoleConfig, ProjectConfig
from murder.llm.harnesses.base import HarnessAdapter
from murder.llm.harnesses.models import HarnessStartSpec, HarnessUsageStatus, HarnessUsageWindow
from murder.llm.harnesses.results import fail_result, ok_result
from murder.llm.harnesses.usage_sampling import UsageSamplingContext, sample_harness_usages


class _StubUsageAdapter(HarnessAdapter):
    kind = "codex"
    usage_collection_mode = "tmux_slash"
    _result = ok_result(
        HarnessUsageStatus(
            harness="codex",
            source="slash:/status",
            fetched_at="2026-06-04T00:00:00+00:00",
            windows=[HarnessUsageWindow(name="5h", percent_used=25.0)],
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


def _config() -> Config:
    role = HarnessRoleConfig(harness="codex")
    return Config(
        project=ProjectConfig(name="repo"),
        collaborator=role,
        default_crow=role,
        crow_handler=CrowHandlerConfig(model="test-model"),
    )


async def _capture_pane(session: str, *, lines: int = 120) -> str:
    del session, lines
    return "idle"


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

    ctx = UsageSamplingContext(config=_config(), repo_root=tmp_path, db=sqlite3.connect(":memory:"))
    stored, failures = asyncio.run(sample_harness_usages(ctx))

    assert stored == 1
    assert failures == 0
    assert len(creates) == 1
    assert creates[0][2] == ["stub-harness"]
    assert len(inserted) == 1
    assert kills == [creates[0][0], creates[0][0]]


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

    ctx = UsageSamplingContext(config=_config(), repo_root=tmp_path, db=sqlite3.connect(":memory:"))
    stored, failures = asyncio.run(sample_harness_usages(ctx))

    assert stored == 0
    assert failures == 1
    assert len(creates) == 1
    assert kills == [creates[0], creates[0]]
