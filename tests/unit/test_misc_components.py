"""Headless unit tests for the t055 misc-component StoreComponent migration.

Covers:
  - EscalationStrip as a StoreComponent: bind store, subscribe on mount,
    render on change, unsubscribe on unmount.
  - _user_visible persistence: bridge-supplied show= is honoured and preserved
    across subsequent store-driven re-renders.

All tests are purely headless — no real Textual app, no asyncio event loop.
Uses the real EscalationsStore (high-fidelity BaseStore contract) rather than
hand-rolled fakes, following the pattern in test_store_component_base.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import pytest

from murder.app.service.client_api import EscalationsSnapshot, EscalationSummary
from murder.app.tui.escalation_strip import EscalationStrip
from murder.app.tui.stores.escalations import EscalationsStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row(
    escalation_id: int,
    *,
    ticket_id: str | None = "t-1",
    reason: str = "blocked",
    severity: int = 2,
    to_recipient: str = "user",
    ticket_status: str | None = None,
) -> EscalationSummary:
    return EscalationSummary(
        id=escalation_id,
        ticket_id=ticket_id,
        severity=severity,
        reason=reason,
        to_recipient=to_recipient,
        body_path=None,
        ticket_status=ticket_status,
    )


def _snapshot(
    *active: EscalationSummary,
    history: tuple[EscalationSummary, ...] = (),
    key: str = "k1",
) -> EscalationsSnapshot:
    return EscalationsSnapshot(
        active=active,
        history=history,
        as_of=datetime.now(timezone.utc),
        invalidation_key=key,
    )


def _make_strip() -> EscalationStrip:
    """Instantiate EscalationStrip in headless mode.

    EscalationStrip.__init__ calls Static.__init__ which tries to call
    self.update() — that requires a Textual app context.  We patch update()
    and display to make the strip safe for headless use.
    """
    with patch.object(EscalationStrip, "update", lambda self, *a, **kw: None):
        strip = EscalationStrip()
    # Make display settable without a real Textual reactive.
    strip.__dict__["display"] = True
    return strip


# ---------------------------------------------------------------------------
# Store binding — subscribe on mount / unsubscribe on unmount
# ---------------------------------------------------------------------------


def test_strip_subscribes_on_mount() -> None:
    """bind_stores + on_mount wires the subscription to the escalations store."""
    store = EscalationsStore()
    strip = _make_strip()
    strip.bind_stores(escalations=store)

    renders: list[Any] = []
    original_rfs = strip.refresh_from_snapshot
    strip.refresh_from_snapshot = lambda snap, **kw: renders.append(snap)  # type: ignore[method-assign]

    strip.on_mount()

    # Initial paint fires one render.
    assert len(renders) == 1

    # Store change fires another.
    store.ingest_snapshot(_snapshot(_row(1)))
    assert len(renders) == 2


def test_strip_unsubscribes_on_unmount() -> None:
    """on_unmount stops future store-change renders."""
    store = EscalationsStore()
    strip = _make_strip()
    strip.bind_stores(escalations=store)

    renders: list[Any] = []
    strip.refresh_from_snapshot = lambda snap, **kw: renders.append(snap)  # type: ignore[method-assign]

    strip.on_mount()
    strip.on_unmount()

    renders.clear()
    store.ingest_snapshot(_snapshot(_row(1)))
    assert renders == []


def test_strip_render_on_change_reflects_new_snapshot() -> None:
    """After store change the strip renders the updated snapshot content."""
    store = EscalationsStore()
    strip = _make_strip()
    strip.bind_stores(escalations=store)

    # Capture rendered snapshots via the real refresh_from_snapshot, but patch
    # the Textual-calling side-effects (update, display) to stay headless.
    rendered: list[Any] = []
    real_rfs = EscalationStrip.refresh_from_snapshot

    def _patched_rfs(self: EscalationStrip, snap: Any, **kw: Any) -> None:
        rendered.append(snap)
        # Minimal: record active_rows without Textual side-effects.
        self._active_rows = list(snap.active)

    with patch.object(EscalationStrip, "refresh_from_snapshot", _patched_rfs):
        strip.on_mount()
        rendered.clear()  # discard initial paint

        store.ingest_snapshot(_snapshot(_row(10, reason="urgent")))

    # The snapshot forwarded to refresh_from_snapshot should contain the new row.
    assert len(rendered) == 1
    assert rendered[0].active[0].id == 10


def test_strip_noop_when_no_store_bound() -> None:
    """StoreComponent mixin is a no-op when no store is bound."""
    strip = _make_strip()
    # Do NOT call bind_stores — should not raise.
    strip.on_mount()
    strip.on_unmount()


# ---------------------------------------------------------------------------
# _user_visible persistence across bridge and store paths
# ---------------------------------------------------------------------------


def test_user_visible_persists_across_store_rerender() -> None:
    """show=False passed by the bridge is remembered; store re-renders respect it."""
    strip = _make_strip()

    updates: list[str] = []
    strip.update = updates.append  # type: ignore[assignment]

    row = _row(1)
    # Bridge call with show=False — user hid the strip.
    strip.refresh_from_snapshot(_snapshot(row), show=False)
    assert strip._user_visible is False
    assert strip.display is False

    # Store-driven re-render (no show kwarg) must stay hidden.
    strip.refresh_from_snapshot(_snapshot(row, key="k2"))
    assert strip.display is False


def test_user_visible_restored_after_set_user_visible() -> None:
    """set_user_visible(True) re-shows the strip and persists for next render."""
    strip = _make_strip()
    strip.update = lambda *a, **kw: None  # type: ignore[assignment]

    row = _row(1)
    strip.refresh_from_snapshot(_snapshot(row), show=False)
    assert strip.display is False

    strip.set_user_visible(True)
    assert strip.display is True
    assert strip._user_visible is True

    # Subsequent store-driven render should stay visible.
    strip.refresh_from_snapshot(_snapshot(row, key="k3"))
    assert strip.display is True


def test_user_visible_defaults_true() -> None:
    """Default _user_visible is True so the strip shows on first store render."""
    strip = _make_strip()
    assert strip._user_visible is True


def test_bridge_show_true_updates_user_visible() -> None:
    """Bridge can restore visibility by passing show=True."""
    strip = _make_strip()
    strip.update = lambda *a, **kw: None  # type: ignore[assignment]

    row = _row(1)
    strip.refresh_from_snapshot(_snapshot(row), show=False)
    assert strip._user_visible is False

    strip.refresh_from_snapshot(_snapshot(row, key="k2"), show=True)
    assert strip._user_visible is True
    assert strip.display is True
