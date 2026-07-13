"""Side-channel usage sampling never controls terminal harnesses."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from murder.config import Config, CrowHandlerConfig, HarnessRoleConfig, ProjectConfig
from murder.llm.harnesses.base import HarnessAdapter
from murder.llm.harnesses.models import HarnessUsageStatus, HarnessUsageWindow
from murder.llm.harnesses.results import ok_result
from murder.llm.harnesses.usage_sampling import (
    UsageSamplingContext,
    harness_kinds_to_sample,
    sample_harness_usages,
)
from murder.state.persistence.schema import init_db


class _TmuxUsageAdapter(HarnessAdapter):
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


class _HttpUsageAdapter(_TmuxUsageAdapter):
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

    async def collect_usage_status(self, session: str):
        del session
        return self._result


def _mixed_pool_config() -> Config:
    role = HarnessRoleConfig(harness="codex", harnesses=["codex", "cursor"])
    return Config(
        project=ProjectConfig(name="repo"),
        collaborator=role,
        default_crow=role,
        crow_handler=CrowHandlerConfig(model="test-model"),
    )


def _db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    init_db(connection)
    return connection


def test_harness_kinds_to_sample_keeps_usage_inventory_broad(monkeypatch) -> None:
    ctx = UsageSamplingContext(config=_mixed_pool_config(), repo_root=Path("/tmp"), db=None)
    monkeypatch.setattr(
        "murder.llm.harnesses.usage_sampling.REGISTRY",
        {"codex": _TmuxUsageAdapter, "cursor": _HttpUsageAdapter},
    )

    assert harness_kinds_to_sample(ctx, modes=None) == ["codex", "cursor"]
    assert harness_kinds_to_sample(ctx, modes={"http"}) == ["cursor"]


def test_background_sampler_persists_http_and_skips_tmux_without_terminal_io(
    monkeypatch, tmp_path: Path
) -> None:
    requested: list[str] = []
    inserted: list[str] = []

    def get_harness(kind: str):
        requested.append(kind)
        assert kind == "cursor", "tmux usage must not create a legacy probe harness"
        return _HttpUsageAdapter()

    monkeypatch.setattr(
        "murder.llm.harnesses.usage_sampling.REGISTRY",
        {"codex": _TmuxUsageAdapter, "cursor": _HttpUsageAdapter},
    )
    monkeypatch.setattr("murder.llm.harnesses.usage_sampling.get_harness", get_harness)
    monkeypatch.setattr(
        "murder.llm.harnesses.usage_sampling.insert_harness_usage_snapshot",
        lambda db, status: inserted.append(status.harness),
    )
    ctx = UsageSamplingContext(config=_mixed_pool_config(), repo_root=tmp_path, db=_db())

    stored, failures = asyncio.run(sample_harness_usages(ctx))

    assert (stored, failures) == (1, 0)
    assert requested == ["cursor"]
    assert inserted == ["cursor"]


def test_tmux_only_background_sampling_is_a_noop_without_a_bound_controller(
    monkeypatch, tmp_path: Path
) -> None:
    role = HarnessRoleConfig(harness="codex")
    config = Config(
        project=ProjectConfig(name="repo"),
        collaborator=role,
        default_crow=role,
        crow_handler=CrowHandlerConfig(model="test-model"),
    )
    monkeypatch.setattr(
        "murder.llm.harnesses.usage_sampling.REGISTRY", {"codex": _TmuxUsageAdapter}
    )
    ctx = UsageSamplingContext(config=config, repo_root=tmp_path, db=_db())

    assert asyncio.run(sample_harness_usages(ctx)) == (0, 0)
