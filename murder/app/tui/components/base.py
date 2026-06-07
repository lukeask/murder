"""StoreComponent mixin — React useSyncExternalStore shape for Textual widgets.

This mixin wires the data-layer stores to a widget's render method.  It is
the single integration point that four Phase 2 wave-2 tickets subclass.

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

Migration / optional binding
-----------------------------
During Phase 2 migration, binding a store is OPTIONAL.  A component with no
store bound stays bridge-driven (coordinator calls refresh_from_snapshot
directly) and the mixin is a no-op.  t056 makes binding required and removes
the optional path.

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

    Public API (stable contract for t052–t055)
    ------------------------------------------
    bind_stores(**stores) -> None
        Inject one or more stores by name.  Call before on_mount (e.g. from
        the parent layout/app) or inside __init__.  Binding is optional during
        migration; when no stores are bound the mixin is a no-op.

    _on_store_change() -> None
        Subscribe callback; also available for bridge callers that want to
        trigger a re-render without injecting the snapshot themselves.

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
        """
        parent_mount = getattr(super(), "on_mount", None)
        if parent_mount is not None:
            parent_mount()

        bound = getattr(self, "_bound_stores", None)
        if not bound:
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
