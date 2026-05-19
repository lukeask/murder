"""Pane-state regex tests for the cursor harness.

Fixtures captured 2026-05-01 from `agent v2026.04.30-4edb302` running
inside tmux; see `tests/fixtures/cursor_panes/README` workflow:

    tmux capture-pane -p -t <session> -S -300 > <name>.txt
    # then strip ANSI before saving

Re-capture if the cursor TUI redesigns its input frame or status line.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from murder.harnesses.cursor import CursorAdapter
from murder.harnesses.cursor_usage import _jwt_exp
from murder.harnesses.models import (
    HarnessStartSpec,
    HarnessUsageStatus,
    HarnessUsageWindow,
)
from tests.unit._harness_assertions import assert_adapter_basics

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
def test_idle_states_are_idle_and_ready_and_not_busy(adapter: CursorAdapter, fixture: str) -> None:
    pane = _load(fixture)
    assert adapter.is_ready(pane), f"{fixture}: should be ready"
    assert adapter.is_idle(pane), f"{fixture}: should be idle"
    assert not adapter.is_busy(pane), f"{fixture}: should not be busy"


@pytest.mark.parametrize(
    "fixture",
    ["busy_composing.txt", "busy_running_tool.txt"],
)
def test_busy_states_are_busy_and_not_idle(adapter: CursorAdapter, fixture: str) -> None:
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
    pane = (
        "ctrl+c to stop\n"
        + "\n" * 50
        + (
            " ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄\n"
            "  → Add a follow-up\n"
            " ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀\n"
            "  Composer 2                              Auto-run\n"
            "  /tmp/murder-smoke · master\n"
        )
    )
    assert adapter.is_idle(pane)
    assert not adapter.is_busy(pane)


def test_trailing_blank_rows_do_not_hide_idle_prompt(adapter: CursorAdapter) -> None:
    pane = (
        "Cursor Agent\n"
        "v2026.05.09-0afadcc\n"
        "\n"
        " ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄\n"
        "  → Plan, search, build anything\n"
        " ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀\n"
        "  Composer 2                              Auto-run\n"
        "  /tmp/murder-smoke · master\n" + "\n" * 40
    )
    assert adapter.is_ready(pane)
    assert adapter.is_idle(pane)
    assert not adapter.is_busy(pane)


def test_note_parser_requires_end_and_strips_cursor_chrome(
    adapter: CursorAdapter,
) -> None:
    pane = "\n".join(
        [
            ">>> NOTE: implemented the CLI path",
            "with a second useful line",
            ">>> END",
            "\u2192 Add a follow-up",
            "Composer 2 · 8.2% · 3 files edited                                  Auto-run",
            "ctrl+r to review edits",
            ">>> DONE",
        ]
    )

    assert adapter.detect_notes(pane) == ["implemented the CLI path\nwith a second useful line"]
    assert adapter.detect_done(pane)


def test_note_parser_ignores_unterminated_note_with_cursor_chrome(
    adapter: CursorAdapter,
) -> None:
    pane = "\n".join(
        [
            ">>> NOTE: this should not capture the footer",
            "\u2192 Add a follow-up",
            "Composer 2 · 8.2% · 3 files edited                                  Auto-run",
            "ctrl+r to review edits",
        ]
    )

    assert adapter.detect_notes(pane) == []


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


async def test_cursor_collect_usage_status_uses_cursor_api_helper(monkeypatch) -> None:
    def fake_get_usage_status() -> HarnessUsageStatus:
        return HarnessUsageStatus(
            harness="cursor",
            source="cursor-api:GetCurrentPeriodUsage",
            fetched_at="2026-05-04T12:00:00+00:00",
            plan="pro",
            windows=[
                HarnessUsageWindow(
                    name="current_period",
                    percent_used=31.5,
                    reset_at="2026-05-05T00:00:00+00:00",
                )
            ],
        )

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("murder.harnesses.cursor_usage.get_usage_status", fake_get_usage_status)
    monkeypatch.setattr("murder.harnesses.cursor.asyncio.to_thread", fake_to_thread)
    result = await CursorAdapter().attach("sess", Path("/repo")).collect_usage_status()
    assert result.ok
    assert result.data is not None
    assert result.data.plan == "pro"
    assert result.data.windows[0].percent_used == 31.5


def test_cursor_adapter_contract_basics() -> None:
    pane = _load("idle_first_load.txt")
    assert_adapter_basics(CursorAdapter(), pane, Path("/repo"))


def test_cursor_jwt_exp_parses_urlsafe_payload() -> None:
    payload = json.dumps({"exp": 1735689600}, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    token = f"header.{encoded}.sig"
    assert _jwt_exp(token) == 1735689600
