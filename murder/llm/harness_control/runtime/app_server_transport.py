"""App-server JSON-RPC implementation of the actuator's effect transport.

Keystroke effects are rejected here: adapters must lower semantic actions into
``AppServerRpcEffect`` values before emission.  The connection port is
duck-typed so this module does not depend on a concrete W2 connection class.
"""

from __future__ import annotations

from typing import Protocol

from murder.llm.harness_control.model.actions import (
    AcpRpcEffect,
    AgentSdkEffect,
    AppServerRpcEffect,
    DelayProfile,
)


class AppServerRpcPort(Protocol):
    """Minimal JSON-RPC surface needed to emit ``AppServerRpcEffect`` values."""

    async def request(
        self,
        method: str,
        params: dict[str, object] | None = None,
    ) -> object: ...

    async def notify(
        self,
        method: str,
        params: dict[str, object] | None = None,
    ) -> None: ...

    async def respond(
        self,
        id: str | int,
        *,
        result: dict[str, object] | None = None,
        error: dict[str, object] | None = None,
    ) -> None: ...


class AppServerEffectTransport:
    """Dispatch app-server RPC effects; reject keystroke effects."""

    def __init__(self, connection: AppServerRpcPort) -> None:
        self._connection = connection

    async def send_literal_keys(self, text: str, *, inter_key_delay: DelayProfile | None) -> None:
        raise TypeError(
            "app-server transport does not accept keystroke effects; "
            "adapters must lower them to AppServerRpcEffect"
        )

    async def paste_buffer(self, text: str) -> None:
        raise TypeError(
            "app-server transport does not accept keystroke effects; "
            "adapters must lower them to AppServerRpcEffect"
        )

    async def send_named_key(self, key: str) -> None:
        raise TypeError(
            "app-server transport does not accept keystroke effects; "
            "adapters must lower them to AppServerRpcEffect"
        )

    async def invoke_app_server_rpc(self, effect: AppServerRpcEffect) -> None:
        if effect.response_id is not None:
            await self._connection.respond(
                effect.response_id,
                result=effect.response_result,
                error=effect.response_error,
            )
            return
        if effect.expects_response:
            await self._connection.request(effect.method, effect.params)
            return
        await self._connection.notify(effect.method, effect.params)

    async def invoke_agent_sdk(self, effect: AgentSdkEffect) -> None:
        raise TypeError("app-server transport cannot invoke Agent SDK effects")

    async def invoke_acp_rpc(self, effect: AcpRpcEffect) -> None:
        raise TypeError("app-server transport cannot invoke ACP RPC")


__all__ = [
    "AppServerEffectTransport",
    "AppServerRpcPort",
]
