"""Pane-state regex tests for the cursor harness.

Fixtures captured 2026-05-01 from `agent v2026.04.30-4edb302` running
inside tmux; see `tests/fixtures/cursor_panes/README` workflow:

    tmux capture-pane -p -t <session> -S -300 > <name>.txt
    # then strip ANSI before saving

Re-capture if the cursor TUI redesigns its input frame or status line.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from murder.harnesses.cursor import CursorAdapter
from tests.unit.harness_contracts import assert_adapter_basics

FIXTURES = Path(__file__).parent.parent / "fixtures" / "cursor_panes"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def adapter() -> CursorAdapter:
    return CursorAdapter()


@pytest.mark.parametrize(
    "fixture",
    ["idle_first_load.txt", "idle_after_first_turn.txt"],
)
def test_idle_states_are_idle_and_ready_and_not_busy(
    adapter: CursorAdapter, fixture: str
) -> None:
    pane = _load(fixture)
    assert adapter.is_ready(pane), f"{fixture}: should be ready"
    assert adapter.is_idle(pane), f"{fixture}: should be idle"
    assert not adapter.is_busy(pane), f"{fixture}: should not be busy"


@pytest.mark.parametrize(
    "fixture",
    ["busy_composing.txt", "busy_running_tool.txt"],
)
def test_busy_states_are_busy_and_not_idle(
    adapter: CursorAdapter, fixture: str
) -> None:
    pane = _load(fixture)
    assert adapter.is_busy(pane), f"{fixture}: should be busy"
    assert not adapter.is_idle(pane), f"{fixture}: should not be idle"
    # Ready stays True while busy — the agent is booted, just not waiting.
    assert adapter.is_ready(pane), f"{fixture}: should be ready (booted)"


def test_trust_prompt_blocks_ready(adapter: CursorAdapter) -> None:
    pane = (
        "Workspace Trust Required\n"
        "Do you trust the contents of this directory?\n"
        "[a] Trust this workspace\n"
        "[q] Quit\n"
    )
    assert not adapter.is_ready(pane)
    assert not adapter.is_idle(pane)
    assert not adapter.is_busy(pane)


def test_stale_busy_marker_in_scrollback_doesnt_flag_busy(
    adapter: CursorAdapter,
) -> None:
    """If the agent went busy, then completed, the historical 'ctrl+c to stop'
    can still be in the pane scrollback; only the live tail counts."""
    pane = "ctrl+c to stop\n" + "\n" * 50 + (
        " ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄\n"
        "  → Add a follow-up\n"
        " ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀\n"
        "  Composer 2                              Auto-run\n"
        "  /tmp/murder-smoke · master\n"
    )
    assert adapter.is_idle(pane)
    assert not adapter.is_busy(pane)


async def test_set_model_sends_cursor_slash_command(monkeypatch) -> None:
    calls: list[tuple[str, str, bool, bool]] = []

    async def fake_send_keys(
        session: str, text: str, *, literal: bool = True, enter: bool = True
    ) -> None:
        calls.append((session, text, literal, enter))

    monkeypatch.setattr("murder.tmux.send_keys", fake_send_keys)
    assert await CursorAdapter().set_model("sess", "Composer 2")
    assert calls == [("sess", "/model Composer 2", True, True)]


async def test_set_autonomy_sends_auto_run_command(monkeypatch) -> None:
    calls: list[tuple[str, str, bool, bool]] = []

    async def fake_send_keys(
        session: str, text: str, *, literal: bool = True, enter: bool = True
    ) -> None:
        calls.append((session, text, literal, enter))

    from murder.harnesses.models import HarnessStartSpec

    monkeypatch.setattr("murder.tmux.send_keys", fake_send_keys)
    result = await CursorAdapter().initialize_defaults(
        "sess", HarnessStartSpec(cwd=Path("/repo"), auto_run=True)
    )
    assert result.ok
    assert calls == [("sess", "/auto-run on", True, True)]


async def test_harness_session_set_model_returns_typed_result(monkeypatch) -> None:
    async def fake_send_keys(
        session: str, text: str, *, literal: bool = True, enter: bool = True
    ) -> None:
        del session, text, literal, enter

    monkeypatch.setattr("murder.tmux.send_keys", fake_send_keys)
    result = await CursorAdapter().attach("sess", Path("/repo")).set_model("Composer 2")
    assert result.ok


def test_cursor_adapter_contract_basics() -> None:
    pane = _load("idle_first_load.txt")
    assert_adapter_basics(CursorAdapter(), pane, Path("/repo"))
