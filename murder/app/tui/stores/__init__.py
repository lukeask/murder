"""Store base contract for the TUI data/render split.

Implements the React useSyncExternalStore shape so these classes carry over
verbatim to a future web UI.
"""

from murder.app.tui.stores.base import BaseStore, Store, StoreRegistry

__all__ = ["BaseStore", "Store", "StoreRegistry"]
