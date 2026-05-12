from __future__ import annotations

from pathlib import Path

from murder.harnesses.base import HarnessAdapter
from murder.harnesses.models import HarnessStartSpec


class _FakeAdapter(HarnessAdapter):
    kind = "fake"
    crow_system_prompt = ""

    def __init__(self) -> None:
        super().__init__(startup_model="model-x")
        self.commands: list[str] = []

    def startup_cmd(self, cwd: Path) -> list[str]:
        self.commands.append(f"start:{cwd}")
        return ["fake-cli"]

    def is_ready(self, pane_text: str) -> bool:
        return "READY" in pane_text

    def is_idle(self, pane_text: str) -> bool:
        return "IDLE" in pane_text

    def is_busy(self, pane_text: str) -> bool:
        return "BUSY" in pane_text

    def extract_last_message(self, pane_text: str) -> str | None:
        return pane_text if pane_text else None

    def format_nudge(self, msg: str) -> str:
        return msg

    async def set_model(self, session: str, model: str) -> bool:
        self.commands.append(f"model:{session}:{model}")
        return True

    async def initialize_defaults(self, session: str, spec: HarnessStartSpec):
        from murder.harnesses.results import ok_result

        self.commands.append(f"defaults:{session}:{spec.auto_run}")
        return ok_result()


async def test_session_start_runs_full_sequence(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    async def fake_create_session(name: str, cwd: Path, command: list[str]) -> None:
        calls.append(("create", (name, str(cwd), tuple(command))))

    panes = iter(["booting", "READY\nIDLE", "READY\nIDLE", "READY\nIDLE"])

    async def fake_capture_pane(name: str, lines: int = 120) -> str:
        calls.append(("capture", (name, lines)))
        return next(panes)

    monkeypatch.setattr("murder.tmux.create_session", fake_create_session)
    monkeypatch.setattr("murder.tmux.capture_pane", fake_capture_pane)

    adapter = _FakeAdapter()
    session = adapter.attach("sess-1", Path("/repo"))
    result = await session.start(HarnessStartSpec(cwd=Path("/repo"), auto_run=True))

    assert result.ok
    assert calls[0] == ("create", ("sess-1", "/repo", ("fake-cli",)))
    assert adapter.commands == [
        "start:/repo",
        "model:sess-1:model-x",
        "defaults:sess-1:True",
    ]


async def test_session_start_fails_when_never_ready(monkeypatch) -> None:
    async def fake_create_session(name: str, cwd: Path, command: list[str]) -> None:
        del name, cwd, command

    async def fake_capture_pane(name: str, lines: int = 120) -> str:
        del name, lines
        return "booting"

    monkeypatch.setattr("murder.tmux.create_session", fake_create_session)
    monkeypatch.setattr("murder.tmux.capture_pane", fake_capture_pane)

    adapter = _FakeAdapter()
    session = adapter.attach("sess-2", Path("/repo"))
    result = await session.start(
        HarnessStartSpec(cwd=Path("/repo"), ready_timeout_s=0.01, poll_interval_s=0.01)
    )

    assert not result.ok
    assert "not ready" in (result.message or "")
