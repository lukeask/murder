"""Service-owned application protocol.

The protocol in this package is the only public client surface. Clients use
the websocket request, subscription, and terminal contracts defined here;
service implementation details are not client APIs.
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
