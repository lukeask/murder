"""The murder web/mobile bridge: an aiohttp server that serves the compiled
React frontend and relays a WebSocket endpoint 1:1 to the murder unix bus.

The bridge does NOT interpret the wire protocol — it is a dumb byte relay
(newline-framed JSON in both directions). The browser implements the full
protocol. See ``murder.web.bridge`` for the server.
"""

from __future__ import annotations

from murder.web.bridge import (
    AiohttpMissingError,
    create_app,
    resolve_assets_dir,
    run_server,
)

__all__ = [
    "AiohttpMissingError",
    "create_app",
    "resolve_assets_dir",
    "run_server",
]
