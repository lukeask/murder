"""MurderApp footer binding visibility via check_action."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from murder.config import PlannerConfig
from murder.app.tui.app import MurderApp


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(
        repo_root=Path("/tmp/murder-test"),
        config=SimpleNamespace(
            project=SimpleNamespace(name="murder-test"),
            tui=SimpleNamespace(refresh_ms=1000),
            runtime=SimpleNamespace(session_name_template="murder_{project}_{role}{suffix}"),
            planner=PlannerConfig(harness="claude_code"),  # type: ignore[arg-type]
        ),
        get_ticket_status=lambda ticket_id: None,
        get_ticket_carve_snapshot=lambda ticket_id: None,
        capture_pane=lambda session, lines=200: "",
    )


def _app(view: str) -> MurderApp:
    app = MurderApp(_runtime())
    app._view = view  # noqa: SLF001 - check_action reads _view directly
    return app


def test_check_action_sidebar_and_raw_only_in_planning_and_crows() -> None:
    for view in ("planning", "crows"):
        app = _app(view)
        assert app.check_action("toggle_sidebar", ()) is True
        assert app.check_action("toggle_collab_raw", ()) is True
    app = _app("schedule")
    assert app.check_action("toggle_sidebar", ()) is False
    assert app.check_action("toggle_collab_raw", ()) is False


def test_check_action_schedule_actions_only_in_dispatch() -> None:
    app = _app("schedule")
    assert app.check_action("schedule_apply_carve", ()) is True
    assert app.check_action("kick_ready", ()) is True
    for view in ("planning", "crows"):
        app = _app(view)
        assert app.check_action("schedule_apply_carve", ()) is False
        assert app.check_action("kick_ready", ()) is False


def test_check_action_focus_chat_hidden_in_dispatch() -> None:
    for view in ("planning", "crows"):
        assert _app(view).check_action("focus_chat", ()) is True
    assert _app("schedule").check_action("focus_chat", ()) is False


def test_check_action_global_bindings_always_enabled() -> None:
    for view in ("planning", "crows", "schedule"):
        app = _app(view)
        for action in (
            "open_settings",
            "view_planning",
            "view_crows",
            "view_schedule",
            "show_help_force",
            "refresh_now",
        ):
            assert app.check_action(action, ()) is True


def test_view_switch_updates_header_active_tab() -> None:
    """Ctrl+2/Ctrl+3 must move the header's active-tab highlight, not just focus."""
    app = MurderApp(_runtime())

    async def _run() -> None:
        async with app.run_test() as pilot:
            header = app._header  # noqa: SLF001
            assert header is app._layout.header  # noqa: SLF001 - single live instance
            assert header._view == "planning"  # noqa: SLF001

            await pilot.press("ctrl+2")
            await pilot.pause()
            assert app._view == "crows"  # noqa: SLF001
            assert header._view == "crows"  # noqa: SLF001
            # render() yields a Content; its .markup carries the active-tab
            # styling spans (str() drops them), so assert on markup.
            markup = header.render().markup
            assert "[b" in markup and "]crows[/" in markup

            await pilot.press("ctrl+3")
            await pilot.pause()
            assert header._view == "schedule"  # noqa: SLF001
            assert "]dispatch[/" in header.render().markup

    asyncio.run(_run())
