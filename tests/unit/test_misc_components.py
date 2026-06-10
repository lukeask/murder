"""Tests for EscalationStrip-specific StoreComponent behavior.

The generic StoreComponent subscribe/unsubscribe/render-on-change protocol is
covered in test_store_component_base.py.  This file pins only what is specific
to EscalationStrip:

COOKBOOK = store change re-renders with the new snapshot content.
EDGE CASES = `_user_visible` persistence — a bridge-supplied show= is remembered
across subsequent store-driven re-renders.

Headless — no real Textual app, no event loop.  Uses the real EscalationsStore
(high-fidelity BaseStore) rather than hand-rolled fakes.

TODO(support): the patched-`update` headless-strip builder below could move to
simulators.py if other widget tests need a headless Static.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from murder.app.tui.escalation_strip import EscalationStrip
from murder.app.tui.stores.escalations import EscalationsStore
from tests.support.factories import (
    factory_escalation_row,
    factory_escalations_snapshot,
)


def _make_strip() -> EscalationStrip:
    """Instantiate EscalationStrip in headless mode.

    EscalationStrip.__init__ calls Static.__init__ which tries to call
    self.update() — that requires a Textual app context.  We patch update()
    and make display settable to keep the strip safe for headless use.
    """
    with patch.object(EscalationStrip, "update", lambda self, *a, **kw: None):
        strip = EscalationStrip()
    strip.__dict__["display"] = True
    return strip


# ============================================================
# === COOKBOOK ===============================================
# ============================================================


def test_strip_render_on_change_reflects_new_snapshot() -> None:
    """After a store change the strip renders the updated snapshot content."""
    store = EscalationsStore()
    strip = _make_strip()
    strip.bind_stores(escalations=store)

    rendered: list[Any] = []

    def _patched_rfs(self: EscalationStrip, snap: Any, **kw: Any) -> None:
        rendered.append(snap)
        self._active_rows = list(snap.active)

    with patch.object(EscalationStrip, "refresh_from_snapshot", _patched_rfs):
        strip.on_mount()
        rendered.clear()  # discard initial paint

        store.ingest_snapshot(
            factory_escalations_snapshot(factory_escalation_row(10, reason="urgent"))
        )

    assert len(rendered) == 1
    assert rendered[0].active[0].id == 10


# ============================================================
# === EDGE CASES =============================================
# ============================================================


def test_user_visible_persists_across_store_rerender() -> None:
    """show=False passed by the bridge is remembered; store re-renders respect it."""
    strip = _make_strip()
    strip.update = lambda *a, **kw: None  # type: ignore[assignment]

    row = factory_escalation_row(1)
    # Bridge call with show=False — user hid the strip.
    strip.refresh_from_snapshot(factory_escalations_snapshot(row), show=False)
    assert strip._user_visible is False
    assert strip.display is False

    # Store-driven re-render (no show kwarg) must stay hidden.
    strip.refresh_from_snapshot(factory_escalations_snapshot(row, key="k2"))
    assert strip.display is False


def test_user_visible_restored_after_set_user_visible() -> None:
    """set_user_visible(True) re-shows the strip and persists for next render."""
    strip = _make_strip()
    strip.update = lambda *a, **kw: None  # type: ignore[assignment]

    row = factory_escalation_row(1)
    strip.refresh_from_snapshot(factory_escalations_snapshot(row), show=False)
    assert strip.display is False

    strip.set_user_visible(True)
    assert strip.display is True
    assert strip._user_visible is True

    # Subsequent store-driven render should stay visible.
    strip.refresh_from_snapshot(factory_escalations_snapshot(row, key="k3"))
    assert strip.display is True


def test_bridge_show_true_updates_user_visible() -> None:
    """Bridge can restore visibility by passing show=True."""
    strip = _make_strip()
    strip.update = lambda *a, **kw: None  # type: ignore[assignment]

    row = factory_escalation_row(1)
    strip.refresh_from_snapshot(factory_escalations_snapshot(row), show=False)
    assert strip._user_visible is False

    strip.refresh_from_snapshot(factory_escalations_snapshot(row, key="k2"), show=True)
    assert strip._user_visible is True
    assert strip.display is True
