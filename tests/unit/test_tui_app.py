from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from textual.app import ComposeResult

from murder.config import PlannerConfig
from murder.service.client_api import CrowSessionSummary, CrowSnapshot
from murder.tui.app import MurderApp


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

    asyncio.run(_run())


def test_ctrl_y_toggles_focused_crow_tile_in_crows_view() -> None:
    app = _QuietMurderApp()
    snap = CrowSnapshot(
        sessions=(_session(),),
        as_of=datetime.now(timezone.utc),
        invalidation_key="k",
    )

    async def _run() -> None:
        async with app.run_test() as pilot:
            app._crows.render_from_snapshot(snap)  # noqa: SLF001 - focused regression seam
            app._crows.roster_add_rogue("rogue-cursor-test")  # noqa: SLF001
            app._crows.render_from_snapshot(snap)  # noqa: SLF001
            await pilot.pause()
            tile = app._crows.wall.tile_for("rogue-cursor-test")  # noqa: SLF001
            assert tile is not None
            tile.focus()
            await pilot.pause()

            # cursor has a parser → tile starts in parsed mode (raw_mode=False).
            assert tile.raw_mode is False
            app.action_toggle_collab_raw()
            await pilot.pause()
            assert tile.raw_mode is True
            assert app._collab_raw is False  # noqa: SLF001 - crows ctrl+y must not hit planning state

    asyncio.run(_run())


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
