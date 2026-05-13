from __future__ import annotations

from pathlib import Path

import pytest

from murder.harnesses.model_discovery import (
    discover_harness_models,
    probe_invalid_harness_model,
)
from murder.harnesses.models import HarnessStartSpec
from murder.harnesses.results import SimpleResult, fail_result, ok_result


class _FakeModelSession:
    def __init__(
        self,
        *,
        start_result: SimpleResult[None] | None = None,
        collect_result: SimpleResult[list[tuple[str, str]]] | None = None,
        invalid_model_result: SimpleResult[None] | None = None,
    ) -> None:
        self.start_result = start_result or ok_result()
        self.collect_result = collect_result or ok_result([("m1", "Model One")])
        self.invalid_model_result = invalid_model_result or ok_result()
        self.started_specs: list[HarnessStartSpec] = []
        self.invalid_model_probes: list[str] = []

    async def start(self, spec: HarnessStartSpec) -> SimpleResult[None]:
        self.started_specs.append(spec)
        return self.start_result

    async def collect_available_models(
        self,
    ) -> SimpleResult[list[tuple[str, str]]]:
        return self.collect_result

    async def probe_invalid_model(self, model: str) -> SimpleResult[None]:
        self.invalid_model_probes.append(model)
        return self.invalid_model_result


class _FakeModelAdapter:
    model_list_command = "/model"

    def __init__(self, session: _FakeModelSession) -> None:
        self.session = session
        self.attached: list[tuple[str, Path]] = []

    def attach(self, session: str, repo_root: Path) -> _FakeModelSession:
        self.attached.append((session, repo_root))
        return self.session


def _stable_probe_session(
    monkeypatch, *, kind: str = "codex", prefix: str = "models"
) -> str:
    monkeypatch.setattr("murder.harnesses.model_discovery.os.getpid", lambda: 123)
    monkeypatch.setattr(
        "murder.harnesses.model_discovery.time.monotonic_ns",
        lambda: 456_789,
    )
    return f"murder_{prefix}_{kind}_123_456789"


async def test_discover_harness_models_cleans_up_probe_session_on_success(
    monkeypatch,
) -> None:
    session_name = _stable_probe_session(monkeypatch)
    fake_session = _FakeModelSession()
    adapter = _FakeModelAdapter(fake_session)
    killed: list[str] = []

    async def fake_kill_session(session: str) -> None:
        killed.append(session)

    monkeypatch.setattr(
        "murder.harnesses.model_discovery.get_harness",
        lambda kind, startup_model=None: adapter,
    )
    monkeypatch.setattr("murder.tmux.kill_session", fake_kill_session)

    result = await discover_harness_models("codex", Path("/repo"), startup_model="m1")

    assert result.ok, f"probe issue: {result.message}"
    assert result.data == [("m1", "Model One")]
    assert adapter.attached == [(session_name, Path("/repo"))]
    assert killed == [session_name], "cleanup issue: probe tmux session was not killed"
    assert fake_session.started_specs[0].startup_model == "m1"


async def test_discover_harness_models_cleans_up_probe_session_on_parse_failure(
    monkeypatch,
) -> None:
    session_name = _stable_probe_session(monkeypatch)
    fake_session = _FakeModelSession(
        collect_result=fail_result("no model choices parsed")
    )
    adapter = _FakeModelAdapter(fake_session)
    killed: list[str] = []

    async def fake_kill_session(session: str) -> None:
        killed.append(session)

    monkeypatch.setattr(
        "murder.harnesses.model_discovery.get_harness",
        lambda kind, startup_model=None: adapter,
    )
    monkeypatch.setattr("murder.tmux.kill_session", fake_kill_session)

    result = await discover_harness_models("codex", Path("/repo"))

    assert not result.ok, "parsing issue: failed parse unexpectedly succeeded"
    assert "parse failed" in (result.message or ""), result.message
    assert killed == [session_name], "cleanup issue: failed probe tmux session was not killed"


async def test_discover_harness_models_reports_cleanup_failure(monkeypatch) -> None:
    session_name = _stable_probe_session(monkeypatch)
    fake_session = _FakeModelSession()
    adapter = _FakeModelAdapter(fake_session)

    async def fake_kill_session(session: str) -> None:
        assert session == session_name
        raise RuntimeError("tmux refused")

    monkeypatch.setattr(
        "murder.harnesses.model_discovery.get_harness",
        lambda kind, startup_model=None: adapter,
    )
    monkeypatch.setattr("murder.tmux.kill_session", fake_kill_session)

    result = await discover_harness_models("codex", Path("/repo"))

    assert not result.ok, "cleanup issue: cleanup failure unexpectedly succeeded"
    assert "cleanup failed" in (result.message or ""), result.message
    assert session_name in (result.message or "")


@pytest.mark.parametrize("kind", ["cursor", "claude_code", "codex"])
async def test_probe_invalid_harness_model_cleans_up_after_rejection(
    monkeypatch, kind: str
) -> None:
    session_name = _stable_probe_session(
        monkeypatch, kind=kind, prefix="invalid_model"
    )
    fake_session = _FakeModelSession()
    adapter = _FakeModelAdapter(fake_session)
    killed: list[str] = []

    async def fake_kill_session(session: str) -> None:
        killed.append(session)

    monkeypatch.setattr(
        "murder.harnesses.model_discovery.get_harness",
        lambda requested_kind, startup_model=None: adapter,
    )
    monkeypatch.setattr("murder.tmux.kill_session", fake_kill_session)

    result = await probe_invalid_harness_model(kind, Path("/repo"))

    assert result.ok, f"rejection detection issue: {result.message}"
    assert adapter.attached == [(session_name, Path("/repo"))]
    assert fake_session.invalid_model_probes == ["thisisnotarealmodel"]
    assert killed == [session_name], "cleanup issue: probe tmux session was not killed"
    assert fake_session.started_specs[0].startup_model is None


async def test_probe_invalid_harness_model_reports_rejection_detection_failure(
    monkeypatch,
) -> None:
    session_name = _stable_probe_session(
        monkeypatch, kind="cursor", prefix="invalid_model"
    )
    fake_session = _FakeModelSession(
        invalid_model_result=fail_result(
            "cursor did not reject invalid model selection"
        )
    )
    adapter = _FakeModelAdapter(fake_session)
    killed: list[str] = []

    async def fake_kill_session(session: str) -> None:
        killed.append(session)

    monkeypatch.setattr(
        "murder.harnesses.model_discovery.get_harness",
        lambda kind, startup_model=None: adapter,
    )
    monkeypatch.setattr("murder.tmux.kill_session", fake_kill_session)

    result = await probe_invalid_harness_model("cursor", Path("/repo"))

    assert not result.ok, "rejection detection issue: invalid model unexpectedly passed"
    assert "rejection detection failed" in (result.message or ""), result.message
    assert killed == [session_name], "cleanup issue: failed probe tmux session was not killed"


async def test_probe_invalid_harness_model_reports_cleanup_failure(monkeypatch) -> None:
    session_name = _stable_probe_session(
        monkeypatch, kind="codex", prefix="invalid_model"
    )
    fake_session = _FakeModelSession()
    adapter = _FakeModelAdapter(fake_session)

    async def fake_kill_session(session: str) -> None:
        assert session == session_name
        raise RuntimeError("tmux refused")

    monkeypatch.setattr(
        "murder.harnesses.model_discovery.get_harness",
        lambda kind, startup_model=None: adapter,
    )
    monkeypatch.setattr("murder.tmux.kill_session", fake_kill_session)

    result = await probe_invalid_harness_model("codex", Path("/repo"))

    assert not result.ok, "cleanup issue: cleanup failure unexpectedly succeeded"
    assert "cleanup failed" in (result.message or ""), result.message
    assert session_name in (result.message or "")
