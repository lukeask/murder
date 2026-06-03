"""Crows wall projection — cookbook then edge cases."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from textual.app import App, ComposeResult

from murder.service.client_api import CrowSessionSummary, CrowSnapshot
from murder.tui import crows_view as crows_view_mod
from murder.tui.crow_health import Health
from murder.tui.crows_view import CrowEntry, CrowTile, CrowsView, entries_from_snapshot
from murder.tui.themes import crow_tui_variable_defaults, register_crow_themes


class _CrowThemedApp(App[None]):
    def __init__(self) -> None:
        super().__init__()
        register_crow_themes(self)

    def get_theme_variable_defaults(self) -> dict[str, str]:
        return crow_tui_variable_defaults()


class _TileApp(_CrowThemedApp):
    def __init__(self, tile: CrowTile) -> None:
        super().__init__()
        self._tile = tile

    def compose(self) -> ComposeResult:
        yield self._tile


def _session(**kwargs: object) -> CrowSessionSummary:
    defaults = dict(
        agent_id="crow-t001",
        role="crow",
        ticket_id="t001",
        ticket_title="Fix thing",
        status="running",
        session_name="murder_demo_crow_t001",
        harness="cursor",
        last_seen=None,
        started_at=None,
        ticket_status="in_progress",
    )
    defaults.update(kwargs)
    return CrowSessionSummary(**defaults)  # type: ignore[arg-type]


def test_entries_from_snapshot_includes_running_crow() -> None:
    snap = CrowSnapshot(
        sessions=(_session(),),
        as_of=datetime.now(timezone.utc),
        invalidation_key="k",
    )
    entries = entries_from_snapshot(snap)
    assert len(entries) == 1
    assert entries[0].agent_id == "crow-t001"


def test_entries_from_snapshot_skips_handlers() -> None:
    snap = CrowSnapshot(
        sessions=(
            _session(agent_id="crow_handler-t001", role="crow_handler", harness=""),
            _session(agent_id="planning_handler-plan", role="planning_handler", harness=""),
            _session(),
        ),
        as_of=datetime.now(timezone.utc),
        invalidation_key="k",
    )
    entries = entries_from_snapshot(snap)
    assert [e.agent_id for e in entries] == ["crow-t001"]


def test_crow_tile_first_empty_parsed_capture_shows_status() -> None:
    tile = CrowTile(
        CrowEntry(
            agent_id="claude-rogue-test",
            ticket_id="",
            ticket_title="claude-rogue-test",
            harness="claude_code",
            status="running",
            session="murder_repo_crow_claude_rogue_test",
            health=Health.GREEN,
        )
    )

    async def _run() -> None:
        app = _TileApp(tile)
        async with app.run_test() as pilot:
            # claude_code has a parser → tile starts in parsed mode; no toggle needed.
            await pilot.pause()  # let the revealed ChatLog get a real width first
            tile.set_parsed([], "claude_code")
            await pilot.pause()
            rendered = "\n".join(
                strip.text
                for strip in tile._chat_log.lines  # noqa: SLF001 - regression test for blank parsed tiles
            )
            assert "no parsed transcript visible yet" in rendered

    asyncio.run(_run())


def test_crow_tile_set_parsed_rerenders_when_width_changes() -> None:
    # A RichLog written to while display:false (size 0) caches its line Strips at
    # min-width and does not reflow when later revealed. The first parse can land
    # before the freshly-toggled tile is laid out, so set_parsed must re-render
    # when the ChatLog width changes — not dedup on unchanged turns alone — or the
    # transcript paints blank forever despite the rows being present. (The visual
    # symptom only reproduces in a real terminal, so this guards the dedup logic.)
    turns = [("user", "hi there"), ("assistant", "response text here")]
    tile = CrowTile(
        CrowEntry(
            agent_id="antigrav-rogue-test",
            ticket_id="",
            ticket_title="antigrav-rogue-test",
            harness="antigravity",
            status="running",
            session="murder_repo_crow_antigrav_rogue_test",
            health=Health.GREEN,
        )
    )

    class _FakeChatLog:
        def __init__(self) -> None:
            self.size = SimpleNamespace(width=0)
            self.set_turns_calls: list[list[tuple[str, str]]] = []

        def set_turns(self, turns: list[tuple[str, str]]) -> None:
            self.set_turns_calls.append(list(turns))

        def add_status(self, text: str) -> None:
            del text

    fake = _FakeChatLog()
    tile._chat_log = fake  # type: ignore[assignment]  # noqa: SLF001 - regression seam

    # First parse lands while the chat log is still size 0/min-width (hidden /
    # pre-layout): defer rather than cache a narrow render.
    tile.set_parsed(turns, "antigravity")
    assert len(fake.set_turns_calls) == 0
    # Tile gains a real width → render now.
    fake.size.width = 46
    tile.set_parsed(turns, "antigravity")
    assert len(fake.set_turns_calls) == 1
    # Same turns, same width → dedup, no re-render.
    tile.set_parsed(turns, "antigravity")
    assert len(fake.set_turns_calls) == 1
    # A later resize must re-flow the cached lines even though the turns are unchanged.
    fake.size.width = 80
    tile.set_parsed(turns, "antigravity")
    assert len(fake.set_turns_calls) == 2


def test_crow_tile_jk_scroll_when_not_scrollable_does_not_crash() -> None:
    # j/k in parsed mode call the ChatLog scroll actions directly; when the
    # transcript isn't scrollable those raise SkipAction. The direct call bypasses
    # the binding dispatcher that would swallow it, so it must be suppressed or a
    # stray keypress with the tile focused crashes the whole TUI.
    from textual import events

    tile = CrowTile(
        CrowEntry(
            agent_id="rg",
            ticket_id="",
            ticket_title="rg",
            harness="antigravity",
            status="running",
            session="s",
            health=Health.GREEN,
        )
    )

    async def _run() -> None:
        app = _TileApp(tile)
        async with app.run_test() as pilot:
            tile.action_toggle_view()  # parsed mode → j/k handled
            await pilot.pause()
            # No transcript content → nothing scrollable → action raises SkipAction.
            tile.on_key(events.Key("k", None))
            tile.on_key(events.Key("j", None))
            assert app.is_running

    asyncio.run(_run())


class _CrowsApp(_CrowThemedApp):
    def __init__(self, view: CrowsView) -> None:
        super().__init__()
        self._view = view

    def compose(self) -> ComposeResult:
        yield self._view


def test_crows_view_refresh_tails_uses_keyword_lines_capture() -> None:
    calls: list[tuple[str, int]] = []

    async def capture(session: str, *, lines: int) -> str:
        calls.append((session, lines))
        return "Antigravity reply is visible"

    view = CrowsView(capture_pane=capture)  # type: ignore[arg-type]
    snap = CrowSnapshot(
        sessions=(
            _session(
                agent_id="rogue-antigravity-test",
                ticket_id="",
                ticket_title="rogue-antigravity-test",
                session_name="murder_repo_crow_antigravity_rogue_test",
                harness="antigravity",
            ),
        ),
        as_of=datetime.now(timezone.utc),
        invalidation_key="k",
    )

    async def _run() -> None:
        app = _CrowsApp(view)
        async with app.run_test() as pilot:
            view.render_from_snapshot(snap)
            view.roster_add_rogue("rogue-antigravity-test")
            view.render_from_snapshot(snap)
            await view.refresh_tails()
            await pilot.pause()
            tile = view.wall.tile_for("rogue-antigravity-test")
            assert tile is not None
            rendered = "\n".join(
                strip.text
                for strip in tile._raw_log.lines  # noqa: SLF001 - regression test for blank raw tiles
            )
            assert rendered == "Antigravity reply is visible"

    asyncio.run(_run())
    assert calls == [("murder_repo_crow_antigravity_rogue_test", 40)]


def test_crows_view_parsed_refresh_renders_keyword_lines_capture(monkeypatch) -> None:
    calls: list[tuple[str, int]] = []

    async def capture(session: str, *, lines: int) -> str:
        calls.append((session, lines))
        return "raw antigravity transcript"

    monkeypatch.setattr(
        crows_view_mod,
        "_parse_tile_text",
        lambda pane_text, harness_kind: [("assistant", f"parsed {harness_kind} reply")],
    )

    view = CrowsView(capture_pane=capture)  # type: ignore[arg-type]
    snap = CrowSnapshot(
        sessions=(
            _session(
                agent_id="rogue-antigravity-test",
                ticket_id="",
                ticket_title="rogue-antigravity-test",
                session_name="murder_repo_crow_antigravity_rogue_test",
                harness="antigravity",
            ),
        ),
        as_of=datetime.now(timezone.utc),
        invalidation_key="k",
    )

    async def _run() -> None:
        app = _CrowsApp(view)
        async with app.run_test() as pilot:
            view.render_from_snapshot(snap)
            view.roster_add_rogue("rogue-antigravity-test")
            view.render_from_snapshot(snap)
            await pilot.pause()
            tile = view.wall.tile_for("rogue-antigravity-test")
            assert tile is not None
            tile.action_toggle_view()
            await pilot.pause()  # let layout run so ChatLog gets a real width before parsing
            await view.refresh_tails()
            await pilot.pause()
            rendered = "\n".join(
                strip.text
                for strip in tile._chat_log.lines  # noqa: SLF001 - regression test for blank parsed tiles
            )
            assert "parsed antigravity reply" in rendered

    asyncio.run(_run())
    assert calls
    assert all(call == ("murder_repo_crow_antigravity_rogue_test", 400) for call in calls)


def test_crows_view_parsed_refresh_shows_status_when_capture_fails() -> None:
    async def capture(session: str, *, lines: int) -> str:
        del session, lines
        raise crows_view_mod.PaneCaptureError("gone")

    view = CrowsView(capture_pane=capture)  # type: ignore[arg-type]
    snap = CrowSnapshot(
        sessions=(
            _session(
                agent_id="rogue-cursor-test",
                ticket_id="",
                ticket_title="rogue-cursor-test",
                session_name="murder_repo_crow_cursor_rogue_test",
                harness="cursor",
            ),
        ),
        as_of=datetime.now(timezone.utc),
        invalidation_key="k",
    )

    async def _run() -> None:
        app = _CrowsApp(view)
        async with app.run_test() as pilot:
            view.render_from_snapshot(snap)
            view.roster_add_rogue("rogue-cursor-test")
            view.render_from_snapshot(snap)
            await pilot.pause()
            tile = view.wall.tile_for("rogue-cursor-test")
            assert tile is not None
            # cursor has a parser → tile starts in parsed mode; no toggle needed.
            await view.refresh_tails()
            await pilot.pause()
            rendered = "\n".join(
                strip.text
                for strip in tile._chat_log.lines  # noqa: SLF001 - regression test for blank parsed tiles
            )
            assert "session vanished" in rendered

    asyncio.run(_run())


def test_crows_view_wall_uses_fractional_grid_tracks() -> None:
    view = CrowsView()
    snap = CrowSnapshot(
        sessions=(
            _session(
                agent_id="rogue-a",
                ticket_id="",
                ticket_title="rogue-a",
                session_name="murder_repo_crow_cursor_rogue_a",
                harness="cursor",
            ),
            _session(
                agent_id="rogue-b",
                ticket_id="",
                ticket_title="rogue-b",
                session_name="murder_repo_crow_cursor_rogue_b",
                harness="cursor",
            ),
        ),
        as_of=datetime.now(timezone.utc),
        invalidation_key="k",
    )

    async def _run() -> None:
        app = _CrowsApp(view)
        async with app.run_test() as pilot:
            view.render_from_snapshot(snap)
            view.roster_add_rogue("rogue-a")
            view.roster_add_rogue("rogue-b")
            view.render_from_snapshot(snap)
            await pilot.pause()
            assert all(track.unit.name == "FRACTION" for track in view.wall.styles.grid_columns)
            assert all(track.unit.name == "FRACTION" for track in view.wall.styles.grid_rows)

    asyncio.run(_run())


class _KillCaptureCrowsApp(_CrowsApp):
    def __init__(self, view: CrowsView) -> None:
        super().__init__(view)
        self.kills: list[str] = []

    def on_crows_view_kill_requested(self, event: CrowsView.KillRequested) -> None:
        self.kills.append(event.agent_id)


def test_roster_kill_confirm_posts_kill_requested() -> None:
    view = CrowsView()
    snap = CrowSnapshot(
        sessions=(
            _session(
                agent_id="cursor-rogue-test",
                ticket_id="",
                ticket_title="cursor-rogue-test",
                session_name="murder_repo_crow_cursor_rogue_test",
                harness="cursor",
            ),
        ),
        as_of=datetime.now(timezone.utc),
        invalidation_key="k",
    )

    async def _run() -> None:
        app = _KillCaptureCrowsApp(view)
        async with app.run_test() as pilot:
            view.render_from_snapshot(snap)
            view.roster_add_rogue("cursor-rogue-test")
            view.render_from_snapshot(snap)
            await pilot.pause()
            view.roster.focus_first_row()
            await pilot.pause()
            view.roster.action_kill_confirm()
            await pilot.pause()
            assert view.roster._kill_pending == "cursor-rogue-test"  # noqa: SLF001
            assert app.kills == []
            view.roster.action_kill_confirm()
            await pilot.pause()
            assert view.roster._kill_pending is None  # noqa: SLF001
            assert app.kills == ["cursor-rogue-test"]

    asyncio.run(_run())


def _kill_snapshot() -> CrowSnapshot:
    return CrowSnapshot(
        sessions=(_session(agent_id="crow-t001", ticket_id="t001"),),
        as_of=datetime.now(timezone.utc),
        invalidation_key="k",
    )


def test_roster_ctrl_m_ctrl_m_murders_via_real_keys() -> None:
    """ctrl+m arms, a second ctrl+m confirms — driven through key dispatch."""
    view = CrowsView()

    async def _run() -> None:
        app = _KillCaptureCrowsApp(view)
        async with app.run_test() as pilot:
            view.render_from_snapshot(_kill_snapshot())
            await pilot.pause()
            view.roster.focus_first_row()
            await pilot.pause()
            await pilot.press("ctrl+m")
            await pilot.pause()
            assert view.roster._kill_pending == "crow-t001"  # noqa: SLF001
            assert app.kills == []
            # Arming must not also toggle the pane (enter/ctrl+m share a keycode).
            assert view.roster.pane_visible == frozenset()
            await pilot.press("ctrl+m")
            await pilot.pause()
            assert view.roster._kill_pending is None  # noqa: SLF001
            assert app.kills == ["crow-t001"]

    asyncio.run(_run())


def test_roster_ctrl_m_then_m_murders_via_real_keys() -> None:
    """ctrl+m arms, then a bare ``m`` confirms — driven through key dispatch."""
    view = CrowsView()

    async def _run() -> None:
        app = _KillCaptureCrowsApp(view)
        async with app.run_test() as pilot:
            view.render_from_snapshot(_kill_snapshot())
            await pilot.pause()
            view.roster.focus_first_row()
            await pilot.pause()
            await pilot.press("ctrl+m")
            await pilot.pause()
            assert view.roster._kill_pending == "crow-t001"  # noqa: SLF001
            await pilot.press("m")
            await pilot.pause()
            assert view.roster._kill_pending is None  # noqa: SLF001
            assert app.kills == ["crow-t001"]

    asyncio.run(_run())
