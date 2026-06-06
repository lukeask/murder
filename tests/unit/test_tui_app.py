from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from textual.app import ComposeResult

from murder.config import PlannerConfig
from murder.app.service.client_api import CrowSessionSummary, CrowSnapshot
from murder.app.tui.app import MurderApp, _format_delay, _parse_delay_command
from murder.app.tui.chat_input import ChatInput
from murder.app.tui.crow_health import Health
from murder.app.tui.crows_view import CrowEntry, CrowTile


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


def _session(**kwargs: object) -> CrowSessionSummary:
    defaults = dict(
        agent_id="rogue-cursor-test",
        role="crow",
        ticket_id="",
        ticket_title="rogue-cursor-test",
        status="running",
        session_name="murder_repo_crow_cursor_rogue_test",
        harness="cursor",
        last_seen=None,
        started_at=None,
        ticket_status="in_progress",
    )
    defaults.update(kwargs)
    return CrowSessionSummary(**defaults)  # type: ignore[arg-type]


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


def test_interval_pane_refresh_swallows_transient_bus_timeout() -> None:
    # A slow capture_pane RPC raising TimeoutError must skip the tick, not
    # crash the TUI message pump (regression for the interval _refresh_pane
    # being awaited directly instead of run in an exit_on_error=False worker).
    app = _QuietMurderApp()

    async def _boom(session: str, lines: int = 200) -> str:
        del session, lines
        raise asyncio.TimeoutError

    async def _run() -> None:
        async with app.run_test() as pilot:
            app._mirror.set_capture_pane(_boom)  # noqa: SLF001 - regression seam
            app._mirror.set_session("murder_repo_crow_cursor_rogue_test")  # noqa: SLF001
            app._refresh_pane()  # noqa: SLF001 - the interval callback
            await pilot.pause()
            await pilot.pause()
            assert app.is_running
            await pilot.pause()  # let the refresh worker settle before shutdown

    asyncio.run(_run())


def test_ctrl_y_toggles_focused_crow_tile_in_crows_view() -> None:
    app = _QuietMurderApp()
    snap = CrowSnapshot(
        sessions=(
            _session(
                agent_id="rogue-claude-test",
                ticket_title="rogue-claude-test",
                session_name="murder_repo_crow_claude_rogue_test",
                harness="claude_code",
            ),
        ),
        as_of=datetime.now(timezone.utc),
        invalidation_key="k",
    )

    async def _run() -> None:
        async with app.run_test() as pilot:
            app._crows.render_from_snapshot(snap)  # noqa: SLF001 - focused regression seam
            app._crows.roster_add_rogue("rogue-claude-test")  # noqa: SLF001
            app._crows.render_from_snapshot(snap)  # noqa: SLF001
            await pilot.pause()
            tile = app._crows.wall.tile_for("rogue-claude-test")  # noqa: SLF001
            assert tile is not None
            tile.focus()
            await pilot.pause()

            # claude_code has a parser → tile starts in parsed mode (raw_mode=False).
            assert tile.raw_mode is False
            app.action_toggle_collab_raw()
            await pilot.pause()
            assert tile.raw_mode is True
            assert app._collab_raw is False  # noqa: SLF001 - crows ctrl+y must not hit planning state
            await pilot.pause()  # let the immediate tile recapture worker settle before shutdown

    asyncio.run(_run())


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
    app.on_crow_tile_choice_prompt_confirmed(
        CrowTile.ChoicePromptConfirmed(entry, 2, "No, exit")
    )
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


def test_planner_chat_defaults_to_parsed_not_raw_mirror() -> None:
    app = _PlanningMurderApp()

    async def _run() -> None:
        async with app.run_test() as pilot:
            app._chat_target_agent_id = "planner-alpha"  # noqa: SLF001
            app._chat_target_label = "planner: alpha"  # noqa: SLF001
            app._apply_mode()  # noqa: SLF001
            await pilot.pause()

            assert app._collab_raw is False  # noqa: SLF001
            assert app._collab_chat.display is True  # noqa: SLF001
            assert app._mirror.display is False  # noqa: SLF001

    asyncio.run(_run())


def test_ctrl_y_in_planning_requires_chat_pane_focus() -> None:
    app = _PlanningMurderApp()

    async def _run() -> None:
        async with app.run_test() as pilot:
            app._chat_target_agent_id = "planner-alpha"  # noqa: SLF001
            app._apply_mode()  # noqa: SLF001
            await pilot.pause()

            app.set_focus(app._plan_doc)  # noqa: SLF001
            await pilot.pause()
            app.action_toggle_collab_raw()  # noqa: SLF001
            await pilot.pause()

            assert app._collab_raw is False  # noqa: SLF001
            assert app._collab_chat.display is True  # noqa: SLF001

    asyncio.run(_run())


def test_ctrl_y_toggles_planner_chat_when_pane_focused() -> None:
    app = _PlanningMurderApp()

    async def _run() -> None:
        async with app.run_test() as pilot:
            app._chat_target_agent_id = "planner-alpha"  # noqa: SLF001
            app._apply_mode()  # noqa: SLF001
            await pilot.pause()

            app._collab_chat.focus()  # noqa: SLF001
            await pilot.pause()
            app.action_toggle_collab_raw()  # noqa: SLF001
            await pilot.pause()

            assert app._collab_raw is True  # noqa: SLF001
            assert app._mirror.display is True  # noqa: SLF001
            assert app._collab_chat.display is False  # noqa: SLF001

    asyncio.run(_run())


def test_planning_focus_uses_chat_accent_highlight() -> None:
    app = _PlanningMurderApp()

    async def _run() -> None:
        async with app.run_test() as pilot:
            app._chat_target_agent_id = "planner-alpha"  # noqa: SLF001
            app._chat_target_label = "planner: alpha"  # noqa: SLF001
            app._apply_mode()  # noqa: SLF001
            await pilot.pause()

            app._chat.focus()  # noqa: SLF001
            await pilot.pause()
            chat_border = app._chat.styles.border_top  # noqa: SLF001

            app._plans.focus()  # noqa: SLF001
            await pilot.pause()
            assert app._plans.styles.border_top == chat_border  # noqa: SLF001

            app._plan_doc.focus()  # noqa: SLF001
            await pilot.pause()
            assert app._plan_doc.styles.border_top == chat_border  # noqa: SLF001

            app._collab_chat.focus()  # noqa: SLF001
            await pilot.pause()
            assert app._collab_chat.styles.border_top == chat_border  # noqa: SLF001

    asyncio.run(_run())


def test_rename_while_chatting_rogue_submits_crow_rename() -> None:
    app = _PlanningMurderApp()
    submitted: list[dict[str, object]] = []

    async def _submit_command(**kwargs: object) -> dict[str, object]:
        submitted.append(dict(kwargs))
        return {
            "handled": True,
            "old_agent_id": "cursor-rogue-test",
            "agent_id": "cursor-rogue-newname",
        }

    async def _run() -> None:
        async with app.run_test() as pilot:
            app.runtime.submit_command = _submit_command  # type: ignore[attr-defined]
            app._chat_target_agent_id = "cursor-rogue-test"  # noqa: SLF001
            await app._handle_colon(":rename newname")  # noqa: SLF001
            await pilot.pause()

            assert len(submitted) == 1
            assert submitted[0]["kind"] == "crow.rename_rogue"
            assert submitted[0]["payload"] == {
                "agent_id": "cursor-rogue-test",
                "name": "newname",
            }
            assert app._chat_target_agent_id == "cursor-rogue-newname"  # noqa: SLF001

    asyncio.run(_run())


def test_m_command_arms_current_chat_target_murder() -> None:
    app = _PlanningMurderApp()

    async def _run() -> None:
        async with app.run_test() as pilot:
            app._chat_target_agent_id = "crow-t001"  # noqa: SLF001
            app._chat_target_label = "t001 cursor"  # noqa: SLF001
            app._sync_chat_recipient()  # noqa: SLF001

            await app._handle_colon(":m")  # noqa: SLF001
            await pilot.pause()

            assert app._chat_murder_pending_agent_id == "crow-t001"  # noqa: SLF001
            assert "murder this crow?" in str(app._chat.border_subtitle)  # noqa: SLF001

    asyncio.run(_run())


def test_chat_murder_confirm_submits_agent_stop_on_m() -> None:
    app = _PlanningMurderApp()
    submitted: list[dict[str, object]] = []

    async def _submit_command(**kwargs: object) -> dict[str, object]:
        submitted.append(dict(kwargs))
        return {"handled": True}

    async def _run() -> None:
        async with app.run_test() as pilot:
            app.runtime.submit_command = _submit_command  # type: ignore[attr-defined]
            app._chat_target_agent_id = "crow-t001"  # noqa: SLF001
            app._chat_target_label = "t001 cursor"  # noqa: SLF001
            app._sync_chat_recipient()  # noqa: SLF001

            await app._handle_colon(":murder")  # noqa: SLF001
            await pilot.pause()
            app.on_chat_input_murder_confirm(ChatInput.MurderConfirm())
            await pilot.pause()

            assert len(submitted) == 1
            assert submitted[0]["kind"] == "agent.stop"
            assert submitted[0]["payload"] == {"agent_id": "crow-t001"}
            assert app._chat_murder_pending_agent_id is None  # noqa: SLF001
            assert app._chat_target_agent_id is None  # noqa: SLF001

    asyncio.run(_run())


def test_chat_murder_cancel_on_other_key() -> None:
    app = _PlanningMurderApp()

    async def _run() -> None:
        async with app.run_test() as pilot:
            app._chat_target_agent_id = "crow-t001"  # noqa: SLF001
            app._chat_target_label = "t001 cursor"  # noqa: SLF001
            app._sync_chat_recipient()  # noqa: SLF001

            await app._handle_colon(":m")  # noqa: SLF001
            await pilot.pause()
            app.on_chat_input_murder_cancel(ChatInput.MurderCancel())
            await pilot.pause()

            assert app._chat_murder_pending_agent_id is None  # noqa: SLF001
            assert app._chat_target_agent_id == "crow-t001"  # noqa: SLF001

    asyncio.run(_run())


def test_crows_focus_and_accent_highlight_regressions() -> None:
    app = _QuietMurderApp()
    snap = CrowSnapshot(
        sessions=(_session(agent_id="rogue-cursor-a"),),
        as_of=datetime.now(timezone.utc),
        invalidation_key="k",
    )

    async def _run() -> None:
        async with app.run_test() as pilot:
            app._crows.render_from_snapshot(snap)  # noqa: SLF001
            app._crows.roster_add_rogue("rogue-cursor-a")  # noqa: SLF001
            app._crows.render_from_snapshot(snap)  # noqa: SLF001
            await pilot.pause()

            app._chat.focus()  # noqa: SLF001
            await pilot.pause()
            chat_border = app._chat.styles.border_top  # noqa: SLF001

            assert app._crows.focus_roster()  # noqa: SLF001
            await pilot.pause()
            assert app._crows.roster.styles.border_top == chat_border  # noqa: SLF001

            assert app._crows.focus_first_tile()  # noqa: SLF001
            await pilot.pause()
            assert app._crows.wall.styles.border_top == chat_border  # noqa: SLF001

            app.action_focus_right()
            await pilot.pause()
            assert app._focus_contains(app._crows.wall)  # noqa: SLF001

            app.action_focus_down()
            await pilot.pause()
            assert app.focused is app._escalations  # noqa: SLF001

            assert app._crows.focus_roster()  # noqa: SLF001
            await pilot.pause()
            app.action_focus_right()
            await pilot.pause()
            assert app._focus_contains(app._crows.wall)  # noqa: SLF001

            assert app._crows.toggle_roster() is False  # noqa: SLF001
            await pilot.pause()
            assert app._crows.focus_first_tile()  # noqa: SLF001
            await pilot.pause()
            app.action_focus_right()
            await pilot.pause()
            assert app._focus_contains(app._crows.wall)  # noqa: SLF001

            app.action_focus_down()
            await pilot.pause()
            assert app.focused is app._escalations  # noqa: SLF001

    asyncio.run(_run())
