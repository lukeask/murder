"""Service-owned application protocol.

The protocol in this package is the only public client surface.  The legacy
``murder.bus`` wire remains an internal compatibility mechanism while the
service is migrated feature by feature.
"""

from murder.app.protocol.wire import (
    APPLICATION_PROTOCOL_VERSION,
    APPLICATION_WIRE_ADAPTER,
    ApplicationWireMessage,
)

__all__ = [
    "APPLICATION_PROTOCOL_VERSION",
    "APPLICATION_WIRE_ADAPTER",
    "ApplicationWireMessage",
]
