"""Tests for murder.app.tui.app (MurderApp) substantive contracts.

COOKBOOK = canonical delay scheduling, choice prompt confirmation, chat
           recipient sync.
EDGE CASES = bus-timeout swallowing in pane tick, delay parsing boundaries,
             colon body forwarding.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from murder.app.tui.app import MurderApp, _format_delay, _parse_delay_command
from murder.app.tui.crow_health import Health
from murder.app.tui.crows_view import CrowEntry, CrowTile
from murder.config import PlannerConfig


async def _capture_pane(session: str, *, lines: int = 200) -> str:
    del session, lines
    return "  hi\n\n  ok\n\n  Auto · 7.3%\n  ~/repo\n"


def _runtime(*, planner_harness: str = "claude_code") -> SimpleNamespace:
    return SimpleNamespace(
        repo_root=Path("/tmp/murder-test"),
        config=SimpleNamespace(
            project=SimpleNamespace(name="murder-test"),
            tui=SimpleNamespace(refresh_ms=1000),
            runtime=SimpleNamespace(session_name_template="murder_{project}_{role}{suffix}"),
            planner=PlannerConfig(harness=planner_harness),  # type: ignore[arg-type]
        ),
        get_ticket_status=lambda ticket_id: None,
        get_ticket_carve_snapshot=lambda ticket_id: None,
        capture_pane=_capture_pane,
    )


class _QuietMurderApp(MurderApp):
    def __init__(self) -> None:
        super().__init__(_runtime())
        self._view = "crows"

    def on_mount(self) -> None:
        self._apply_mode()


class _PlanningMurderApp(MurderApp):
    def __init__(self) -> None:
        super().__init__(_runtime())
        self._view = "planning"

    def on_mount(self) -> None:
        self._apply_mode()


class _ChoicePromptApp(_QuietMurderApp):
    def __init__(self) -> None:
        super().__init__()
        self.sent_commands: list[dict[str, object]] = []

    async def _submit_command(
        self,
        *,
        target_worker: str,
        kind: str,
        payload: dict[str, object],
        timeout_s: float,
        notify_errors: bool = True,
    ):
        del timeout_s, notify_errors
        self.sent_commands.append(
            {
                "target_worker": target_worker,
                "kind": kind,
                "payload": dict(payload),
            }
        )
        return {"handled": True, "session": "murder_demo_crow_t001"}


# ============================================================
# === COOKBOOK ===============================================
# ============================================================


def test_delay_command_schedules_current_chat_target_snapshot() -> None:
    app = _PlanningMurderApp()
    scheduled: list[dict[str, object]] = []

    def _schedule(
        delay_s: float,
        message: str,
        *,
        target_id: str | None,
        target_label: str,
    ) -> None:
        scheduled.append(
            {
                "delay_s": delay_s,
                "message": message,
                "target_id": target_id,
                "target_label": target_label,
            }
        )

    async def _run() -> None:
        app.notify = lambda *args, **kwargs: None  # type: ignore[method-assign]
        app._chat_target_agent_id = "crow-t001"  # noqa: SLF001
        app._chat_target_label = "t001 cursor"  # noqa: SLF001
        app._schedule_delayed_chat = _schedule  # type: ignore[method-assign]

        await app._handle_colon(":delay 3h1m check again")  # noqa: SLF001

        assert scheduled == [
            {
                "delay_s": 10860.0,
                "message": "check again",
                "target_id": "crow-t001",
                "target_label": "t001 cursor",
            }
        ]

    asyncio.run(_run())


def test_delayed_worker_sends_colon_prefixed_body_as_agent_message() -> None:
    app = _PlanningMurderApp()
    submitted: list[dict[str, object]] = []

    async def _submit_command(**kwargs: object) -> dict[str, object]:
        submitted.append(dict(kwargs))
        return {"handled": True}

    async def _run() -> None:
        app.notify = lambda *args, **kwargs: None  # type: ignore[method-assign]
        app.runtime.submit_command = _submit_command  # type: ignore[attr-defined]

        await app._delayed_chat_worker(  # noqa: SLF001
            0,
            ":delay 1h nested",
            target_id="crow-t001",
            target_label="t001 cursor",
        )

        assert submitted == [
            {
                "target_worker": "orchestrator",
                "kind": "agent.message",
                "payload": {"agent_id": "crow-t001", "message": ":delay 1h nested"},
                "timeout_s": 120.0,
            }
        ]

    asyncio.run(_run())


def test_sync_chat_recipient_uses_live_crow_title_label() -> None:
    app = _QuietMurderApp()

    entry = CrowEntry(
        agent_id="codex-rogue-tailwall",
        ticket_id="",
        ticket_title="tailwall",
        harness="codex",
        status="running",
        session="murder_repo_crow_codex_rogue_tailwall",
        health=Health.GREEN,
        model="gpt-5.4",
    )

    # _sync_chat_recipient has no public callback; patching visible_wall_chat_targets
    # is the only drive hook available without a running event loop.
    app._crows.visible_wall_chat_targets = lambda: (  # type: ignore[method-assign]  # noqa: SLF001
        [entry.agent_id],
        {entry.agent_id: entry},
    )
    app._chat_target_agent_id = entry.agent_id  # noqa: SLF001
    app._chat_target_label = entry.agent_id  # noqa: SLF001
    app._sync_chat_recipient()  # noqa: SLF001

    assert str(app._chat.border_title) == "tailwall codex gpt-5.4 rogue"  # noqa: SLF001


def test_choice_prompt_confirmation_drives_pane_with_enter_and_logs_user_input() -> None:
    app = _ChoicePromptApp()
    entry = CrowEntry(
        agent_id="crow-t001",
        ticket_id="t001",
        ticket_title="Fix thing",
        harness="claude_code",
        status="running",
        session="murder_demo_crow_t001",
        health=Health.GREEN,
    )
    coroutines = []

    def _run_worker(coro, *, exclusive: bool, group: str):  # type: ignore[no-untyped-def]
        del exclusive, group
        coroutines.append(coro)
        return SimpleNamespace()

    app.run_worker = _run_worker  # type: ignore[method-assign]
    app.on_crow_tile_choice_prompt_confirmed(CrowTile.ChoicePromptConfirmed(entry, 2, "No, exit"))
    assert len(coroutines) == 1
    asyncio.run(coroutines[0])

    assert app.sent_commands == [
        {
            "target_worker": "orchestrator",
            "kind": "agent.send_key",
            "payload": {
                "agent_id": "crow-t001",
                "key": "2",
                "literal": True,
                "enter": True,
                "log_user_input": "2. No, exit",
            },
        }
    ]


# ============================================================
# === EDGE CASES =============================================
# ============================================================


def test_interval_pane_refresh_swallows_transient_bus_timeout() -> None:
    # A slow capture_pane RPC raising TimeoutError must skip the tick, not
    # crash the TUI message pump (regression for the interval pane tick
    # being awaited directly instead of run in an exit_on_error=False worker).
    app = _QuietMurderApp()
    calls: list[dict[str, object]] = []

    def _run_worker(coro, **kwargs):  # type: ignore[no-untyped-def]
        calls.append({"coro": coro, **kwargs})
        coro.close()
        return SimpleNamespace()

    app.run_worker = _run_worker  # type: ignore[method-assign]

    app._run_pane_tick()  # noqa: SLF001 - the interval callback

    assert len(calls) == 1
    assert calls[0]["exclusive"] is True
    assert calls[0]["group"] == "pane_refresh"
    assert calls[0]["exit_on_error"] is False


def test_delay_command_parses_compact_duration_and_message() -> None:
    assert _parse_delay_command(":delay 5m check status") == (300.0, "check status")
    assert _parse_delay_command(":delay 3h1m anothermessage") == (
        10860.0,
        "anothermessage",
    )
    assert _parse_delay_command(":delay 10s :delay 1h nested") == (
        10.0,
        ":delay 1h nested",
    )
    assert _format_delay(10860.0) == "3h1m"


def test_delay_command_rejects_missing_or_invalid_parts() -> None:
    assert _parse_delay_command(":delay") is None
    assert _parse_delay_command(":delay 5m") is None
    assert _parse_delay_command(":delay 0m nope") is None
    assert _parse_delay_command(":delay 1x nope") is None
    assert _parse_delay_command(":delay 1m2 nope") is None
