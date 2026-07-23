"""High-level helpers over :class:`AgentSdkConnection`."""

from __future__ import annotations

from typing import Any

from murder.llm.harness_control.agent_sdk.connection import AgentSdkConnection

PERMISSION_ALLOW = "allow"
PERMISSION_DENY = "deny"


class AgentSdkClient:
    """Thin helpers for query / interrupt / permission replies."""

    def __init__(self, connection: AgentSdkConnection) -> None:
        self.connection = connection

    async def query(self, prompt: str) -> None:
        await self.connection.query(prompt)

    async def interrupt(self) -> None:
        await self.connection.interrupt()

    async def set_model(self, model: str | None) -> None:
        await self.connection.set_model(model)

    async def allow_tool(
        self,
        request_id: str,
        *,
        updated_input: dict[str, Any] | None = None,
    ) -> None:
        await self.connection.respond_permission(
            request_id,
            behavior=PERMISSION_ALLOW,
            updated_input=updated_input,
        )

    async def deny_tool(
        self,
        request_id: str,
        *,
        message: str = "User denied this action",
        interrupt: bool = False,
    ) -> None:
        await self.connection.respond_permission(
            request_id,
            behavior=PERMISSION_DENY,
            message=message,
            interrupt=interrupt,
        )


__all__ = [
    "PERMISSION_ALLOW",
    "PERMISSION_DENY",
    "AgentSdkClient",
]
