"""TUI component base classes for the Phase 2 store-subscribing widget layer.

Implements the React useSyncExternalStore pattern so the component contract
is a near-mechanical translation to any future web UI.
"""

from murder.app.tui.components.base import StoreComponent

__all__ = ["StoreComponent"]
