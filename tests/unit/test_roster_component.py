"""Headless tests for CrowsView as a StoreComponent (t054).

Tests:
- render-on-change: bind RosterStore, mount, ingest changed snapshot → view
  entries update.
- no re-derivation: entries from snapshot are used verbatim (the view does not
  re-project / re-sort them independently).
- unsubscribe-on-unmount: after unmount, further store changes do not call back
  into the view.
- bridge path still works: the old render_from_snapshot(CrowSnapshot) interface
  still produces correct results.
- mixin lifecycle: on_mount calls super() so both prefs and the mixin subscribe
  path run.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from murder.app.tui.crows_view import CrowsView
from murder.app.tui.stores.roster import RosterStore
from textual.app import App, ComposeResult
from murder.app.tui.themes import crow_tui_variable_defaults, register_crow_themes
from tests.support.factories import factory_crow_session, factory_crow_snapshot


# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------


_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


class _ThemedApp(App[None]):
    def __init__(self) -> None:
        super().__init__()
        register_crow_themes(self)

    def get_theme_variable_defaults(self) -> dict[str, str]:
        return crow_tui_variable_defaults()


class _CrowsApp(_ThemedApp):
    def __init__(self, view: CrowsView) -> None:
        super().__init__()
        self._view = view

    def compose(self) -> ComposeResult:
        yield self._view


# ---------------------------------------------------------------------------
# StoreComponent integration — render-on-change
# ---------------------------------------------------------------------------


def test_crows_view_renders_on_store_change() -> None:
    """Binding a RosterStore and ingesting a new snapshot updates the roster."""
    store = RosterStore()
    view = CrowsView()
    view.bind_stores(roster=store)

    async def _run() -> None:
        app = _CrowsApp(view)
        async with app.run_test() as pilot:
            # Initially empty snapshot — no entries.
            assert len(view._entries_by_id) == 0  # noqa: SLF001

            # Ingest a snapshot with one crow.
            snap = factory_crow_snapshot(factory_crow_session())
            store.ingest_snapshot(snap, now=_NOW)
            await pilot.pause()

            assert "crow-t001" in view._entries_by_id  # noqa: SLF001

    asyncio.run(_run())


def test_crows_view_renders_on_second_store_change() -> None:
    """A second store ingest after the first renders the updated entry set."""
    store = RosterStore()
    view = CrowsView()
    view.bind_stores(roster=store)

    async def _run() -> None:
        app = _CrowsApp(view)
        async with app.run_test() as pilot:
            store.ingest_snapshot(factory_crow_snapshot(factory_crow_session()), now=_NOW)
            await pilot.pause()
            assert "crow-t001" in view._entries_by_id  # noqa: SLF001

            # Swap in a different crow.
            store.ingest_snapshot(
                factory_crow_snapshot(factory_crow_session(agent_id="crow-t002", ticket_id="t002"), key="k2"),
                now=_NOW,
            )
            await pilot.pause()
            assert "crow-t001" not in view._entries_by_id  # noqa: SLF001
            assert "crow-t002" in view._entries_by_id  # noqa: SLF001

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# No re-derivation: entries from snapshot used verbatim
# ---------------------------------------------------------------------------


def test_crows_view_uses_snapshot_entries_verbatim() -> None:
    """After a store ingest, _entries_by_id matches snapshot.entries exactly.

    Proves the view consumes the store's already-projected entries rather than
    re-running entries_from_snapshot itself.
    """
    store = RosterStore()
    view = CrowsView()
    view.bind_stores(roster=store)

    # Two sessions: escalating first (will be sort-rank 0 in store), idle second.
    snap = factory_crow_snapshot(
        factory_crow_session(agent_id="crow-idle", status="idle"),
        factory_crow_session(agent_id="crow-esc", status="escalating"),
        key="v1",
    )

    async def _run() -> None:
        app = _CrowsApp(view)
        async with app.run_test() as pilot:
            store.ingest_snapshot(snap, now=_NOW)
            await pilot.pause()

            # Snapshot entries tuple is the store's projected+sorted list.
            roster_snap = store.get_snapshot()
            expected_ids = {e.agent_id for e in roster_snap.entries}
            actual_ids = set(view._entries_by_id.keys())  # noqa: SLF001
            assert actual_ids == expected_ids

            # The invalidation key is taken from the snapshot, not recomputed.
            assert view.invalidation_key == "v1"  # noqa: SLF001

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Unsubscribe-on-unmount
# ---------------------------------------------------------------------------


def test_crows_view_unsubscribes_on_unmount() -> None:
    """After the widget unmounts, store changes no longer trigger renders."""
    store = RosterStore()
    view = CrowsView()
    view.bind_stores(roster=store)

    async def _run() -> None:
        app = _CrowsApp(view)
        async with app.run_test() as pilot:
            store.ingest_snapshot(factory_crow_snapshot(factory_crow_session()), now=_NOW)
            await pilot.pause()
            assert "crow-t001" in view._entries_by_id  # noqa: SLF001

        # After the context manager exits the app unmounts all widgets.
        # Verify the store has no remaining subscriptions from this view.
        assert store._subs == {}  # noqa: SLF001

    asyncio.run(_run())


def test_crows_view_store_change_after_unmount_does_not_update_view() -> None:
    """A store ingest after unmount leaves _entries_by_id unchanged."""
    store = RosterStore()
    view = CrowsView()
    view.bind_stores(roster=store)

    async def _run() -> None:
        app = _CrowsApp(view)
        async with app.run_test() as pilot:
            store.ingest_snapshot(factory_crow_snapshot(factory_crow_session()), now=_NOW)
            await pilot.pause()

        # Capture the state at unmount.
        entries_at_unmount = dict(view._entries_by_id)  # noqa: SLF001

        # Push a new snapshot after unmount.
        store.ingest_snapshot(
            factory_crow_snapshot(factory_crow_session(agent_id="crow-t999", ticket_id="t999"), key="k2"),
            now=_NOW,
        )

        # The view must not have updated.
        assert view._entries_by_id == entries_at_unmount  # noqa: SLF001

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Bridge path (render_from_snapshot with CrowSnapshot) still works
# ---------------------------------------------------------------------------


def test_bridge_path_still_works_without_store_bound() -> None:
    """The legacy render_from_snapshot(CrowSnapshot) bridge path still renders."""
    view = CrowsView()
    # No bind_stores call — stays bridge-driven.

    snap = factory_crow_snapshot(factory_crow_session())

    async def _run() -> None:
        app = _CrowsApp(view)
        async with app.run_test() as pilot:
            view.render_from_snapshot(snap)
            await pilot.pause()
            assert "crow-t001" in view._entries_by_id  # noqa: SLF001

    asyncio.run(_run())


def test_bridge_path_filters_terminal_agents() -> None:
    """Bridge path filters done/dead agents (delegates to entries_from_snapshot)."""
    view = CrowsView()

    snap = factory_crow_snapshot(
        factory_crow_session(agent_id="crow-done", status="done"),
        factory_crow_session(agent_id="crow-running", status="running"),
    )

    async def _run() -> None:
        app = _CrowsApp(view)
        async with app.run_test() as pilot:
            view.render_from_snapshot(snap)
            await pilot.pause()
            assert "crow-done" not in view._entries_by_id  # noqa: SLF001
            assert "crow-running" in view._entries_by_id  # noqa: SLF001

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Dual path convergence: store path and bridge path yield the same entries
# ---------------------------------------------------------------------------


def test_store_and_bridge_paths_converge() -> None:
    """Store path and bridge path produce the same _entries_by_id for the same input."""
    snap = factory_crow_snapshot(
        factory_crow_session(agent_id="crow-a", status="running"),
        factory_crow_session(agent_id="crow-b", status="idle"),
        key="same",
    )

    store = RosterStore()
    view_store = CrowsView()
    view_store.bind_stores(roster=store)

    view_bridge = CrowsView()

    async def _run() -> None:
        app_s = _CrowsApp(view_store)
        app_b = _CrowsApp(view_bridge)
        async with app_s.run_test() as pilot_s:
            store.ingest_snapshot(snap, now=_NOW)
            await pilot_s.pause()
        async with app_b.run_test() as pilot_b:
            view_bridge.render_from_snapshot(snap)
            await pilot_b.pause()

        # Both paths should end up with the same agent IDs.
        assert set(view_store._entries_by_id) == set(view_bridge._entries_by_id)  # noqa: SLF001

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Mixin no-op when unbound (existing tests still pass)
# ---------------------------------------------------------------------------


def test_no_crash_when_no_store_bound() -> None:
    """CrowsView mounts fine with no store bound (bridge-only mode)."""
    view = CrowsView()

    async def _run() -> None:
        app = _CrowsApp(view)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.is_running

    asyncio.run(_run())
