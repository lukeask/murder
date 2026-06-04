"""MurderApp footer binding visibility via check_action."""

from __future__ import annotations

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
