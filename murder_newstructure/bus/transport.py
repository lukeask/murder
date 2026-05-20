"""Transport ABC — minimal send/recv/close contract.

Framing, protocol logic, and subscription semantics all live above this
layer (in the broker / protocol modules).  A Transport only moves bytes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Transport(ABC):
    """Minimal async byte-transport interface."""

    @abstractmethod
    async def send(self, data: bytes) -> None:
        """Write *data* to the transport."""

    @abstractmethod
    async def recv(self) -> bytes:
        """Read the next chunk from the transport.

        Returns an empty bytes object on clean EOF.
        Raises an exception on error or abrupt disconnect.
        """

    @abstractmethod
    async def close(self) -> None:
        """Close the transport, releasing any underlying resources."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """True while the underlying connection is open."""


__all__ = ["Transport"]
