"""Layout modules for the murder TUI.

A layout module instantiates and binds the StoreComponent widgets,
then exposes a compose() function and named widget references for
app.py to wire into the Textual widget tree and action handlers.
"""

from murder.app.tui.layout.default_layout import DefaultLayout

__all__ = ["DefaultLayout"]
