"""Tests for murder.app.tui.components (StoreComponent mixin).

COOKBOOK = subscribe-on-mount / unsubscribe-on-unmount / initial-paint — the
           canonical "how to use this mixin" pattern, copyable by widget authors.
EDGE CASES = cooperative super() MRO, idempotent unmount, no-op-when-unbound.

All tests are purely headless — no real Textual app, no asyncio event loop.
We use BaseStore from the stores layer (high-fidelity real subscribe/get_snapshot)
and minimal stub components that exercise the mixin contract without importing
any Textual widget.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from murder.app.tui.components import StoreComponent
from murder.app.tui.stores.base import BaseStore

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Snapshot:
    value: int = 0


class _FakeStore(BaseStore[_Snapshot]):
    """High-fidelity store using the real BaseStore contract."""

    def __init__(self, value: int = 0) -> None:
        super().__init__(_Snapshot(value))

    def set_value(self, value: int) -> None:
        self._set(_Snapshot(value))


class _StubWidget(StoreComponent):
    """Minimal component stub — no Textual base, just the mixin.

    Mimics the render entrypoint used by bridge-pattern widgets so the mixin
    can call self.refresh_from_snapshot without knowing the real widget type.
    """

    def __init__(self) -> None:
        self.rendered_snapshots: list[Any] = []

    def refresh_from_snapshot(self, snapshot: _Snapshot) -> None:
        self.rendered_snapshots.append(snapshot)


class _StubWidgetWithOwnLifecycle(StoreComponent):
    """Stub that defines its own on_mount / on_unmount, calling super().

    This is the exact cooperation pattern wave-2 widgets use (e.g. TicketGrid
    calls super().on_mount() after its add_columns setup).
    """

    def __init__(self) -> None:
        self.rendered_snapshots: list[Any] = []
        self.own_mount_called: bool = False
        self.own_unmount_called: bool = False

    def on_mount(self) -> None:
        self.own_mount_called = True
        super().on_mount()

    def on_unmount(self) -> None:
        self.own_unmount_called = True
        super().on_unmount()

    def refresh_from_snapshot(self, snapshot: _Snapshot) -> None:
        self.rendered_snapshots.append(snapshot)


# ============================================================
# === COOKBOOK ===============================================
# ============================================================


def test_subscribe_on_mount() -> None:
    """on_mount subscribes to the bound store."""
    store = _FakeStore(1)
    widget = _StubWidget()
    widget.bind_stores(main=store)

    widget.on_mount()

    # After mount, changing the store's state should trigger a render.
    initial_count = len(widget.rendered_snapshots)
    store.set_value(99)
    assert len(widget.rendered_snapshots) == initial_count + 1


def test_initial_paint_on_mount() -> None:
    """on_mount fires an initial render so the widget is never blank."""
    store = _FakeStore(3)
    widget = _StubWidget()
    widget.bind_stores(main=store)

    widget.on_mount()

    assert len(widget.rendered_snapshots) == 1
    assert widget.rendered_snapshots[0] == _Snapshot(3)


def test_unsubscribe_on_unmount() -> None:
    """on_unmount stops future renders from store changes."""
    store = _FakeStore(0)
    widget = _StubWidget()
    widget.bind_stores(main=store)
    widget.on_mount()
    widget.on_unmount()

    widget.rendered_snapshots.clear()
    store.set_value(100)

    assert widget.rendered_snapshots == []


def test_render_called_on_change() -> None:
    """Store change reads the snapshot and calls refresh_from_snapshot."""
    store = _FakeStore(0)
    widget = _StubWidget()
    widget.bind_stores(main=store)
    widget.on_mount()

    # Reset so we can observe changes only (ignoring the initial paint).
    widget.rendered_snapshots.clear()

    store.set_value(7)
    assert widget.rendered_snapshots == [_Snapshot(7)]


def test_snapshot_read_through() -> None:
    """The snapshot forwarded to refresh_from_snapshot matches get_snapshot()."""
    store = _FakeStore(5)
    widget = _StubWidget()
    widget.bind_stores(main=store)
    widget.on_mount()

    widget.rendered_snapshots.clear()
    store.set_value(42)

    assert widget.rendered_snapshots[0] is store.get_snapshot()
    assert widget.rendered_snapshots[0] == _Snapshot(42)


def test_no_subscription_before_mount() -> None:
    """Binding a store does NOT subscribe until on_mount runs."""
    store = _FakeStore(1)
    widget = _StubWidget()
    widget.bind_stores(main=store)

    store.set_value(42)  # change before mount
    assert widget.rendered_snapshots == []


# ============================================================
# === EDGE CASES =============================================
# ============================================================


def test_component_tolerates_duplicate_store_change_calls() -> None:
    """The component tolerates receiving a change notification twice for the
    same snapshot value — no crash, both calls forwarded (dedup is the store's
    responsibility, not the component's)."""
    store = _FakeStore(1)
    widget = _StubWidget()
    widget.bind_stores(main=store)
    widget.on_mount()
    widget.rendered_snapshots.clear()

    # Manually fire change twice without changing the store value.
    widget._on_store_change()
    widget._on_store_change()
    # Both calls forwarded the same snapshot — no crash, called twice.
    assert len(widget.rendered_snapshots) == 2
    assert all(s == _Snapshot(1) for s in widget.rendered_snapshots)


def test_unmount_is_idempotent() -> None:
    """Calling on_unmount twice does not raise."""
    store = _FakeStore(0)
    widget = _StubWidget()
    widget.bind_stores(main=store)
    widget.on_mount()
    widget.on_unmount()
    widget.on_unmount()  # second call must be safe


def test_multiple_unsubs_on_unmount() -> None:
    """All unsub handles are called, even across multiple stores."""

    class _MultiStoreWidget(StoreComponent):
        def __init__(self) -> None:
            self.rendered_snapshots: list[Any] = []

        def refresh_from_snapshot(self, snapshot: Any) -> None:
            self.rendered_snapshots.append(snapshot)

        def _render_from_stores(self) -> None:
            # Override to handle two stores — just record snapshots from each.
            bound = getattr(self, "_bound_stores", {})
            for s in bound.values():
                self.rendered_snapshots.append(s.get_snapshot())

    store_a = _FakeStore(0)
    store_b = _FakeStore(0)
    widget = _MultiStoreWidget()
    widget.bind_stores(a=store_a, b=store_b)
    widget.on_mount()

    widget.on_unmount()
    widget.rendered_snapshots.clear()

    store_a.set_value(1)
    store_b.set_value(2)
    assert widget.rendered_snapshots == []


def test_noop_when_no_store_bound() -> None:
    """on_mount and on_unmount silently do nothing when bind_stores was never called."""
    widget = _StubWidget()
    widget.on_mount()  # no crash
    widget.on_unmount()  # no crash
    assert widget.rendered_snapshots == []


def test_noop_when_bind_stores_called_with_no_args() -> None:
    """bind_stores() with no kwargs is equivalent to not binding at all."""
    widget = _StubWidget()
    widget.bind_stores()  # empty

    widget.on_mount()
    widget.on_unmount()
    assert widget.rendered_snapshots == []


def test_widget_own_on_mount_and_mixin_both_run() -> None:
    """A widget that defines on_mount calling super() runs both its own logic
    and the mixin's subscribe + initial paint."""
    store = _FakeStore(5)
    widget = _StubWidgetWithOwnLifecycle()
    widget.bind_stores(main=store)

    widget.on_mount()

    assert widget.own_mount_called, "widget's own on_mount was not called"
    assert len(widget.rendered_snapshots) == 1, "mixin initial paint did not run"
    assert widget.rendered_snapshots[0] == _Snapshot(5)


def test_widget_own_on_unmount_and_mixin_both_run() -> None:
    """A widget that defines on_unmount calling super() runs both its own logic
    and the mixin's unsubscribe."""
    store = _FakeStore(0)
    widget = _StubWidgetWithOwnLifecycle()
    widget.bind_stores(main=store)
    widget.on_mount()

    widget.on_unmount()

    assert widget.own_unmount_called, "widget's own on_unmount was not called"
    # Verify unsubscribed — changes no longer trigger renders.
    widget.rendered_snapshots.clear()
    store.set_value(99)
    assert widget.rendered_snapshots == []


def test_mixin_on_mount_safe_without_on_mount_in_mro() -> None:
    """StoreComponent.on_mount must not crash when no parent on_mount exists
    (headless stub, or object as the top of the MRO)."""
    widget = _StubWidget()  # MRO: _StubWidget -> StoreComponent -> object
    widget.bind_stores()
    widget.on_mount()  # must not raise AttributeError


# ---------------------------------------------------------------------------
# No Textual import guard
# ---------------------------------------------------------------------------


def test_no_textual_import_in_components_base() -> None:
    """StoreComponent must not import Textual so it stays headless-testable."""
    import re
    from pathlib import Path

    source = (
        Path(__file__).parent.parent.parent / "murder" / "app" / "tui" / "components" / "base.py"
    ).read_text()
    assert not re.search(r"^\s*(import|from)\s+textual", source, re.MULTILINE)
