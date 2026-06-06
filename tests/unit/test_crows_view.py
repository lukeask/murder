"""Crows wall projection — cookbook then edge cases."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from textual.app import App, ComposeResult

from murder.app.service.client_api import CrowSessionSummary, CrowSnapshot
from murder.app.tui.cc_multiple_choice_wizard import CCMultipleChoiceWizard
from murder.app.tui import crows_view as crows_view_mod
from murder.app.tui.crow_health import Health
from murder.app.tui.crows_view import CrowEntry, CrowTile, CrowsView, entries_from_snapshot
from murder.app.tui.themes import crow_tui_variable_defaults, register_crow_themes


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


def _plain(widget) -> str:
    rendered = widget.render()
    return getattr(rendered, "plain", str(rendered))


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


def test_crow_display_labels_strip_rogue_session_prefix() -> None:
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

    labels = crows_view_mod._crow_display_labels(entry)

    assert labels.name == "tailwall"
    assert labels.harness == "codex"
    assert labels.model == "gpt-5.4"
    assert labels.is_rogue is True


def test_display_name_strips_compact_rogue_prefix() -> None:
    assert crows_view_mod._display_name("codex_rogue_tailwall", "codex") == "tailwall"


def test_crow_display_labels_map_claude_harness() -> None:
    entry = CrowEntry(
        agent_id="claude-rogue-test",
        ticket_id="",
        ticket_title="test",
        harness="claude_code",
        status="running",
        session="murder_repo_crow_claude_rogue_test",
        health=Health.GREEN,
    )

    labels = crows_view_mod._crow_display_labels(entry)

    assert labels.name == "test"
    assert labels.harness == "claude"
    assert labels.model == "—"
    assert labels.is_rogue is True


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
            tile.set_parsed_doc(
                {"harness": "claude_code", "state": "awaiting_input", "condensed": None, "segments": []},
                "claude_code",
            )
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
    doc = {
        "harness": "claude_code",
        "state": "awaiting_input",
        "condensed": None,
        "segments": [
            {"type": "user", "text": "hi there"},
            {"type": "assistant", "phase": "final", "text": "response text here", "elapsed": None},
        ],
    }
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
    tile.set_parsed_doc(doc, "antigravity")
    assert len(fake.set_turns_calls) == 0
    # Tile gains a real width → render now.
    fake.size.width = 46
    tile.set_parsed_doc(doc, "antigravity")
    assert len(fake.set_turns_calls) == 1
    # Same turns, same width → dedup, no re-render.
    tile.set_parsed_doc(doc, "antigravity")
    assert len(fake.set_turns_calls) == 1
    # A later resize must re-flow the cached lines even though the turns are unchanged.
    fake.size.width = 80
    tile.set_parsed_doc(doc, "antigravity")
    assert len(fake.set_turns_calls) == 2


def test_crow_tile_renders_user_segments_distinct_from_assistant_output() -> None:
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
    doc = {
        "harness": "claude_code",
        "state": "awaiting_input",
        "condensed": None,
        "segments": [
            {"type": "user", "text": "show me the status"},
            {"type": "assistant", "phase": "final", "text": "here is the status", "elapsed": "2s"},
        ],
    }

    async def _run() -> None:
        app = _TileApp(tile)
        async with app.run_test() as pilot:
            await pilot.pause()
            tile.set_parsed_doc(doc, "claude_code")
            await pilot.pause()
            rendered = "\n".join(
                strip.text
                for strip in tile._chat_log.lines  # noqa: SLF001 - render invariant
            )
            assert "you: show me the status" in rendered
            assert "claude_code: here is the status" in rendered

    asyncio.run(_run())


def test_crow_tile_renders_live_choice_prompt_as_wizard() -> None:
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
    doc = {
        "harness": "claude_code",
        "state": "awaiting_approval",
        "condensed": None,
        "segments": [
            {
                "type": "choice_prompt",
                "question": "Trust this folder?",
                "options": [
                    {"number": 1, "label": "Yes", "description": None},
                    {"number": 2, "label": "No", "description": None},
                ],
                "footer": "Enter to confirm",
                "selected": 2,
                "answered": False,
                "chosen": None,
            }
        ],
    }

    async def _run() -> None:
        app = _TileApp(tile)
        async with app.run_test() as pilot:
            await pilot.pause()
            tile.set_parsed_doc(doc, "claude_code")
            await pilot.pause()
            assert tile._choice_wizard is not None  # noqa: SLF001
            assert isinstance(tile._choice_wizard, CCMultipleChoiceWizard)  # noqa: SLF001
            assert tile._choice_wizard.prompt.selected_option.number == 2  # noqa: SLF001
            assert tile._choice_wizard._cursor == 1  # noqa: SLF001
            assert tile._choice_wizard.display is True  # noqa: SLF001
            assert tile._chat_log.display is False  # noqa: SLF001

    asyncio.run(_run())


def test_crow_tile_renders_live_choice_prompt_with_stale_doc_state() -> None:
    # Regression (notes/wizard-fail.md): conversation live_state is only carried
    # into the TUI projection at bootstrap, so a prompt that goes live mid-session
    # arrives with a stale doc state (here "working"). The wizard must still
    # trigger off the trailing unanswered choice_prompt segment.
    tile = CrowTile(
        CrowEntry(
            agent_id="planner-test",
            ticket_id="",
            ticket_title="planner-test",
            harness="claude_code",
            status="running",
            session="murder_repo_planner_test",
            health=Health.GREEN,
        )
    )
    doc = {
        "harness": "claude_code",
        "state": "working",
        "condensed": None,
        "segments": [
            {"type": "user", "text": "help me plan"},
            {
                "type": "choice_prompt",
                "question": "Where should we focus?",
                "options": [
                    {"number": 1, "label": "Settle open questions", "description": None},
                    {"number": 2, "label": "Inventory refresh paths", "description": None},
                ],
                "footer": "Enter to select",
                "selected": 1,
                "answered": False,
                "chosen": None,
            },
        ],
    }

    async def _run() -> None:
        app = _TileApp(tile)
        async with app.run_test() as pilot:
            await pilot.pause()
            tile.set_parsed_doc(doc, "claude_code")
            await pilot.pause()
            assert tile._choice_wizard is not None  # noqa: SLF001
            assert tile._choice_wizard.prompt.question == "Where should we focus?"  # noqa: SLF001

    asyncio.run(_run())


def test_crow_tile_renders_answered_choice_prompt_as_static_history() -> None:
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
    doc = {
        "harness": "claude_code",
        "state": "awaiting_input",
        "condensed": None,
        "segments": [
            {
                "type": "choice_prompt",
                "question": "Trust this folder?",
                "options": [
                    {"number": 1, "label": "Yes", "description": None},
                    {"number": 2, "label": "No", "description": None},
                ],
                "footer": "Enter to confirm",
                "answered": True,
                "chosen": 2,
            }
        ],
    }

    async def _run() -> None:
        app = _TileApp(tile)
        async with app.run_test() as pilot:
            await pilot.pause()
            tile.set_parsed_doc(doc, "claude_code")
            await pilot.pause()
            rendered = "\n".join(strip.text for strip in tile._chat_log.lines)  # noqa: SLF001
            assert "Trust this folder?" in rendered
            assert "selected: 2. No" in rendered
            assert tile._choice_wizard is None  # noqa: SLF001

    asyncio.run(_run())


def test_crow_tile_border_uses_name_harness_model_and_not_last_user_message() -> None:
    tile = CrowTile(
        CrowEntry(
            agent_id="codex-rogue-tailwall",
            ticket_id="",
            ticket_title="tailwall",
            harness="codex",
            status="running",
            session="murder_repo_crow_codex_rogue_tailwall",
            health=Health.GREEN,
            model="gpt-5.4",
        )
    )

    async def _run() -> None:
        app = _TileApp(tile)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert str(tile.border_title) == "tailwall codex gpt-5.4 rogue"
            assert str(tile.border_subtitle) == "RUNNING"
            tile._last_user_msg = "please rename the border label"  # noqa: SLF001 - regression seam
            tile._apply_entry()  # noqa: SLF001 - regression seam
            await pilot.pause()
            assert str(tile.border_subtitle) == "please rename the border label"

    asyncio.run(_run())


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
                harness="unknown",
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


def test_crow_tile_has_no_local_transcript_accumulator() -> None:
    tile = CrowTile(
        CrowEntry(
            agent_id="crow-t001",
            ticket_id="t001",
            ticket_title="Fix thing",
            harness="claude_code",
            status="running",
            session="murder_demo_crow_t001",
            health=Health.GREEN,
        )
    )

    assert not hasattr(tile, "_transcript_acc")
    assert not hasattr(tile, "ingest_parsed_frame")


def test_crows_view_parsed_refresh_renders_conversation_projection() -> None:
    view = CrowsView()
    snap = CrowSnapshot(
        sessions=(
            _session(
                agent_id="crow-t001",
                role="crow",
                ticket_id="t001",
                ticket_title="Fix thing",
                session_name="murder_demo_crow_t001",
                harness="claude_code",
            ),
        ),
        as_of=datetime.now(timezone.utc),
        invalidation_key="k",
    )
    doc = {
        "harness": "claude_code",
        "state": "awaiting_input",
        "condensed": None,
        "segments": [
            {"type": "assistant", "phase": "final", "text": "server reply", "elapsed": None},
        ],
    }

    async def _run() -> None:
        app = _CrowsApp(view)
        async with app.run_test() as pilot:
            view.render_from_snapshot(snap)
            view.roster_add_rogue("crow-t001")
            view.render_from_snapshot(snap)
            await pilot.pause()
            tile = view.wall.tile_for("crow-t001")
            assert tile is not None
            view.set_conversation_doc("crow-t001", doc)
            await view.refresh_tails()
            await pilot.pause()
            rendered = "\n".join(strip.text for strip in tile._chat_log.lines)  # noqa: SLF001
            assert "server reply" in rendered

    asyncio.run(_run())


def test_crows_view_parsed_refresh_does_not_call_capture_or_fetch() -> None:
    captured: list[str] = []

    async def capture(session: str, *, lines: int) -> str:
        captured.append(session)
        return "raw only"

    view = CrowsView(capture_pane=capture)  # type: ignore[arg-type]
    snap = CrowSnapshot(
        sessions=(
            _session(
                agent_id="rogue-claude-test",
                ticket_id="",
                ticket_title="rogue-claude-test",
                session_name="murder_repo_crow_claude_rogue_test",
                harness="claude_code",
            ),
        ),
        as_of=datetime.now(timezone.utc),
        invalidation_key="k",
    )

    async def _run() -> None:
        app = _CrowsApp(view)
        async with app.run_test() as pilot:
            view.render_from_snapshot(snap)
            view.roster_add_rogue("rogue-claude-test")
            view.render_from_snapshot(snap)
            await pilot.pause()
            tile = view.wall.tile_for("rogue-claude-test")
            assert tile is not None
            await view.refresh_tails()
            await pilot.pause()
            rendered = "\n".join(
                strip.text
                for strip in tile._chat_log.lines  # noqa: SLF001 - regression test for blank parsed tiles
            )
            assert "parsed transcript unavailable" in rendered

    asyncio.run(_run())
    assert captured == []


def test_crows_view_parsed_refresh_shows_status_when_fetch_unavailable() -> None:
    view = CrowsView()
    snap = CrowSnapshot(
        sessions=(
            _session(
                agent_id="rogue-claude-test",
                ticket_id="",
                ticket_title="rogue-claude-test",
                session_name="murder_repo_crow_claude_rogue_test",
                harness="claude_code",
            ),
        ),
        as_of=datetime.now(timezone.utc),
        invalidation_key="k",
    )

    async def _run() -> None:
        app = _CrowsApp(view)
        async with app.run_test() as pilot:
            view.render_from_snapshot(snap)
            view.roster_add_rogue("rogue-claude-test")
            view.render_from_snapshot(snap)
            await pilot.pause()
            tile = view.wall.tile_for("rogue-claude-test")
            assert tile is not None
            # claude_code has a parser → tile starts in parsed mode; no toggle needed.
            await view.refresh_tails()
            await pilot.pause()
            rendered = "\n".join(
                strip.text
                for strip in tile._chat_log.lines  # noqa: SLF001 - regression test for blank parsed tiles
            )
            assert "parsed transcript unavailable" in rendered

    asyncio.run(_run())


def test_ticket_agent_raw_toggle_populates_raw_log_immediately() -> None:
    """Switching from parsed to raw mode must trigger an immediate capture.

    Ticket agents (claude_code harness) start in parsed mode. The raw_log is
    never written while in parsed mode, so toggling to raw showed an empty tile
    until the next periodic refresh tick. The fix fires a capture immediately on
    any parsed↔raw toggle.
    """
    calls: list[tuple[str, int]] = []

    async def capture(session: str, *, lines: int) -> str:
        calls.append((session, lines))
        return "CC output line 1\nCC output line 2"

    view = CrowsView(capture_pane=capture)  # type: ignore[arg-type]
    snap = CrowSnapshot(
        sessions=(
            _session(
                agent_id="crow-t001",
                role="crow",
                ticket_id="t001",
                ticket_title="Fix thing",
                session_name="murder_demo_crow_t001",
                harness="claude_code",
            ),
        ),
        as_of=datetime.now(timezone.utc),
        invalidation_key="k",
    )

    async def _run() -> None:
        app = _CrowsApp(view)
        async with app.run_test() as pilot:
            view.render_from_snapshot(snap)
            await pilot.pause()
            # Add the ticket agent to pane-visible (simulates user pressing P
            # in the roster). Focus the row first so action_toggle_pane works.
            roster = view._roster  # noqa: SLF001
            roster.focus_agent("crow-t001")
            await pilot.pause()
            roster.action_toggle_pane()
            await pilot.pause()
            tile = view.wall.tile_for("crow-t001")
            assert tile is not None
            # Tile starts in parsed mode (claude_code has a parser).
            assert not tile.raw_mode
            # Populate the parsed log.
            await view.refresh_tails()
            await pilot.pause()
            calls.clear()
            # Toggle to raw mode — must trigger an immediate capture without
            # waiting for the next periodic refresh tick.
            tile.action_toggle_view()
            assert tile.raw_mode
            await pilot.pause()
            # The immediate capture should have populated the raw log.
            rendered = "\n".join(
                strip.text
                for strip in tile._raw_log.lines  # noqa: SLF001
            )
            assert "CC output" in rendered

    asyncio.run(_run())
    # At least one raw-mode capture (lines=40) must have fired immediately.
    assert any(lines == 40 for _, lines in calls)


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


def test_crows_view_roster_shows_compact_rogue_name_harness_and_model() -> None:
    view = CrowsView()
    snap = CrowSnapshot(
        sessions=(
            _session(
                agent_id="codex-rogue-tailwall",
                ticket_id="",
                ticket_title="tailwall",
                session_name="murder_repo_crow_codex_rogue_tailwall",
                harness="codex",
                model="gpt-5.4",
            ),
        ),
        as_of=datetime.now(timezone.utc),
        invalidation_key="k",
    )

    async def _run() -> None:
        app = _CrowsApp(view)
        async with app.run_test() as pilot:
            view.render_from_snapshot(snap)
            view.roster_add_rogue("codex-rogue-tailwall")
            view.render_from_snapshot(snap)
            await pilot.pause()
            row = view.roster._rows["codex-rogue-tailwall"]  # noqa: SLF001 - regression seam
            line1 = _plain(row._line1)  # noqa: SLF001 - regression seam
            line2 = _plain(row._line2)  # noqa: SLF001 - regression seam
            assert "tailwall" in line1
            assert "codex_rogue_tailwall" not in line1
            assert "RUNNING" in line1
            assert "[pane]" in line1
            assert "doing:" in line2
            assert "codex" in line2
            assert "gpt-5.4" in line2
            assert "rogue" in line2

    asyncio.run(_run())


def test_crows_view_chat_target_highlight_tracks_selected_crow() -> None:
    view = CrowsView()
    snap = CrowSnapshot(
        sessions=(
            _session(
                agent_id="codex-rogue-tailwall",
                ticket_id="",
                ticket_title="tailwall",
                session_name="murder_repo_crow_codex_rogue_tailwall",
                harness="codex",
                model="gpt-5.4",
            ),
            _session(
                agent_id="cursor-rogue-scout",
                ticket_id="",
                ticket_title="scout",
                session_name="murder_repo_crow_cursor_rogue_scout",
                harness="cursor",
                model="gpt-5.5",
            ),
        ),
        as_of=datetime.now(timezone.utc),
        invalidation_key="k",
    )

    async def _run() -> None:
        app = _CrowsApp(view)
        async with app.run_test() as pilot:
            view.render_from_snapshot(snap)
            view.roster_add_rogue("codex-rogue-tailwall")
            view.roster_add_rogue("cursor-rogue-scout")
            view.render_from_snapshot(snap)
            await pilot.pause()
            first = view.wall.tile_for("codex-rogue-tailwall")
            second = view.wall.tile_for("cursor-rogue-scout")
            assert first is not None
            assert second is not None

            view.set_chat_target("codex-rogue-tailwall")
            await pilot.pause()
            assert first.has_class("-chat-target")
            assert not second.has_class("-chat-target")

            view.set_chat_target("cursor-rogue-scout")
            await pilot.pause()
            assert not first.has_class("-chat-target")
            assert second.has_class("-chat-target")

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
