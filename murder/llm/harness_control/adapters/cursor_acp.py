"""Cursor-specific ACP adapter: binds the Cursor agent profile by default."""

from __future__ import annotations

from murder.llm.harness_control.acp.agents import get_agent
from murder.llm.harness_control.acp.connection import AcpConnection
from murder.llm.harness_control.adapters.acp import AcpHarnessAdapter


class CursorAcpHarnessAdapter(AcpHarnessAdapter):
    """``AcpHarnessAdapter`` pre-bound to the registered Cursor ACP profile."""

    def __init__(self, connection: AcpConnection | None = None) -> None:
        super().__init__(connection, profile=get_agent("cursor"))


__all__ = ["CursorAcpHarnessAdapter"]
