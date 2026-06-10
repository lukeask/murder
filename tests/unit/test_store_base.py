"""Tests for murder.app.tui.stores.base.

COOKBOOK = canonical subscribe/notify/get_snapshot usage; copyable by widget authors.
EDGE CASES = identity-on-equal invariant, no-fire-on-equal snapshot, registry KeyError.
"""

from __future__ import annotations

from dataclasses import dataclass

from murder.app.tui.stores.base import BaseStore, StoreRegistry


@dataclass(frozen=True)
class _Snap:
    value: int


# ============================================================
# === COOKBOOK ===============================================
# ============================================================


def test_subscribe_callback_fires_on_change() -> None:
    store: BaseStore[_Snap] = BaseStore(_Snap(0))
    calls: list[int] = []
    store.subscribe(lambda: calls.append(1))
    store._set(_Snap(1))
    assert calls == [1]


def test_unsubscribe_handle_stops_callbacks() -> None:
    store: BaseStore[_Snap] = BaseStore(_Snap(0))
    calls: list[int] = []
    unsub = store.subscribe(lambda: calls.append(1))
    unsub()
    store._set(_Snap(1))
    assert calls == []


def test_get_snapshot_returns_latest_value() -> None:
    store: BaseStore[_Snap] = BaseStore(_Snap(0))
    assert store.get_snapshot() == _Snap(0)
    store._set(_Snap(99))
    assert store.get_snapshot() == _Snap(99)


def test_set_changed_snapshot_fires_all_subscribers_exactly_once() -> None:
    store: BaseStore[_Snap] = BaseStore(_Snap(0))
    calls_a: list[int] = []
    calls_b: list[int] = []
    store.subscribe(lambda: calls_a.append(1))
    store.subscribe(lambda: calls_b.append(1))
    store._set(_Snap(1))
    assert calls_a == [1]
    assert calls_b == [1]


def test_store_registry_register_and_get() -> None:
    registry = StoreRegistry()
    store: BaseStore[_Snap] = BaseStore(_Snap(0))
    registry.register("snap", store)
    assert registry.get("snap") is store
    assert "snap" in registry


# ============================================================
# === EDGE CASES =============================================
# ============================================================


def test_set_equal_snapshot_does_not_fire_callbacks() -> None:
    store: BaseStore[_Snap] = BaseStore(_Snap(42))
    calls: list[int] = []
    store.subscribe(lambda: calls.append(1))
    # Two *distinct* objects that are value-equal — adversarial case
    a = _Snap(42)
    b = _Snap(42)
    assert a is not b
    store._set(a)
    store._set(b)
    assert calls == []


def test_equal_snapshot_preserves_object_identity() -> None:
    """get_snapshot() must return the *same* object on an equal _set so that
    downstream identity checks (useSyncExternalStore pattern) never see a
    spurious change."""
    initial = _Snap(7)
    store: BaseStore[_Snap] = BaseStore(initial)
    store._set(_Snap(7))  # equal but distinct
    assert store.get_snapshot() is initial


def test_store_registry_missing_key_raises() -> None:
    registry = StoreRegistry()
    try:
        registry.get("missing")
        raise AssertionError("expected KeyError")
    except KeyError:
        pass


def test_no_textual_import_in_base_module() -> None:
    """Stores must be headless — no Textual dependency."""
    import re
    from pathlib import Path

    source = (
        Path(__file__).parent.parent.parent / "murder" / "app" / "tui" / "stores" / "base.py"
    ).read_text()
    # Check for actual import statements, not mentions in comments/docstrings
    assert not re.search(r"^\s*(import|from)\s+textual", source, re.MULTILINE)
