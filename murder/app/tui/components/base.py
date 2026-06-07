"""StoreComponent mixin — React useSyncExternalStore shape for Textual widgets.

This mixin wires the data-layer stores to a widget's render method.  It is
the integration point for all Phase 2 StoreComponent widgets.

Usage
-----
    class MyWidget(StoreComponent, SomeTextualBase):
        def on_mount(self) -> None:
            # one-time widget setup (e.g. add_columns) MUST run before super()
            # so the first paint lands on an initialised widget.
            self.add_columns("a", "b")
            super().on_mount()          # triggers subscribe + initial paint

        def refresh_from_snapshot(self, snapshot) -> None:
            ...  # idempotent render — safe to call multiple times with same snapshot

Cooperative super() contract
-----------------------------
StoreComponent calls super().on_mount() and super().on_unmount() via
getattr-guards so that it sits safely anywhere in a Textual MRO and in
headless test stubs (where on_mount/on_unmount don't exist on object).

Textual MRO dispatch note
--------------------------
Textual dispatches on_mount/on_unmount to EVERY class in the MRO that defines
the handler.  To prevent double-subscription when a subclass explicitly calls
super().on_mount(), StoreComponent.on_mount is idempotent: if _unsubs is
already set (non-empty), the subscribe+paint step is skipped on subsequent
invocations.

Binding contract
-----------------------------
Store binding via bind_stores() is the standard path.  The layout module
(default_layout.py) ensures every top-level widget is bound before compose().

Two legitimate exceptions where a widget may be mounted without a bound store:

1. Parent-cascade pattern: a container (e.g. DispatchView) is bound to a
   store and manually forwards the snapshot to its children via
   refresh_from_snapshot().  The children are unbound StoreComponents that
   render on demand from the parent's cascade call rather than self-subscription.
   This is intentional, not a migration fallback.

2. Ad-hoc / dynamic conversation_id: ChatLog's conversation_id switches at
   runtime based on app view state.  app.py drives it via set_turns/
   replace_transcript until the status-string model is lifted into the store
   (Phase 3 follow-up).

A widget mounted without a bound store is a no-op for the store-subscription
path — it will not auto-render from any store change.

Render sink
-----------
_on_store_change() calls _render_from_stores(), which by default reads the
single bound store's get_snapshot() and forwards it to refresh_from_snapshot().
Multi-store components override _render_from_stores() instead.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class StoreComponent:
    """Mixin that binds domain stores to a Textual widget.

    Apply before the Textual base class in the MRO::

        class MyWidget(StoreComponent, Static): ...

    Public API
    ----------
    bind_stores(**stores) -> None
        Inject one or more stores by name.  Call before on_mount (e.g. from
        the layout module default_layout.py).  Binding is REQUIRED; a widget
        mounted without a bound store will not auto-render from any store.

    _on_store_change() -> None
        Subscribe callback called by the store on state change.

    _render_from_stores() -> None
        Override in a multi-store component.  Default handles the single-store
        case: reads get_snapshot() and calls self.refresh_from_snapshot(snapshot).
    """

    # ------------------------------------------------------------------
    # Store binding
    # ------------------------------------------------------------------

    def bind_stores(self, **stores: Any) -> None:
        """Store named store references for use during mount.

        Call this before on_mount — from the constructor, the layout module,
        or the app — so that on_mount can set up subscriptions immediately.
        Calling it *after* mount is not supported and will not re-subscribe.
        """
        self._bound_stores: dict[str, Any] = dict(stores)

    # ------------------------------------------------------------------
    # Textual lifecycle hooks
    # ------------------------------------------------------------------

    def on_mount(self) -> None:
        """Subscribe to each bound store and paint the initial snapshot.

        Calls super().on_mount() first so that any Textual base-class or
        sibling-mixin setup runs before the first paint.

        Idempotent: Textual dispatches on_mount to each class in the MRO
        separately, so widgets that call super().on_mount() explicitly would
        trigger this method twice. The guard on ``_unsubs`` prevents
        double-subscription and an initial paint from the second invocation.
        """
        parent_mount = getattr(super(), "on_mount", None)
        if parent_mount is not None:
            parent_mount()

        bound = getattr(self, "_bound_stores", None)
        if not bound:
            return

        # Idempotency guard: skip if already subscribed (happens when a
        # subclass explicitly calls super().on_mount() AND Textual's own MRO
        # dispatch also invokes StoreComponent.on_mount separately).
        if getattr(self, "_unsubs", None):
            return

        unsubs: list[Callable[[], None]] = []
        for store in bound.values():
            unsub = store.subscribe(self._on_store_change)
            unsubs.append(unsub)
        self._unsubs: list[Callable[[], None]] = unsubs

        # Initial paint so the widget is never blank on first render.
        self._on_store_change()

    def on_unmount(self) -> None:
        """Unsubscribe from all bound stores."""
        for unsub in getattr(self, "_unsubs", []):
            unsub()
        self._unsubs = []

        parent_unmount = getattr(super(), "on_unmount", None)
        if parent_unmount is not None:
            parent_unmount()

    # ------------------------------------------------------------------
    # Render sink
    # ------------------------------------------------------------------

    def _on_store_change(self) -> None:
        """Called by the store on every state change; routes to _render_from_stores."""
        self._render_from_stores()

    def _render_from_stores(self) -> None:
        """Read snapshots and call the component's render entrypoint.

        Default: single-store case — reads the first (and only) bound store's
        get_snapshot() and calls self.refresh_from_snapshot(snapshot).

        Override this method in multi-store components to compose snapshots
        before forwarding to the render entrypoint.
        """
        bound = getattr(self, "_bound_stores", None)
        if not bound:
            return

        stores = list(bound.values())
        if len(stores) == 1:
            snapshot = stores[0].get_snapshot()
            self.refresh_from_snapshot(snapshot)  # type: ignore[attr-defined]
        else:
            # Multi-store: subclass must override _render_from_stores.
            raise NotImplementedError(
                f"{type(self).__name__} has {len(stores)} bound stores but does not "
                "override _render_from_stores().  Override it to compose multiple "
                "snapshots into a render call."
            )
