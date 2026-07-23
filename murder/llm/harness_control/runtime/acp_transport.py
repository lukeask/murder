"""ACP JSON-RPC implementation of the actuator's effect transport.

Keystroke effects are rejected here: adapters must lower semantic actions into
``AcpRpcEffect`` values before emission.  The connection port is duck-typed so
this module does not depend on a concrete ACP connection class beyond the RPC
surface (plus optional ``prompt_in_flight`` for ``session/prompt``).
"""

from __future__ import annotations

from typing import Protocol

from murder.llm.harness_control.model.actions import (
    AcpRpcEffect,
    AgentSdkEffect,
    AppServerRpcEffect,
    DelayProfile,
)


class AcpRpcPort(Protocol):
    """Minimal JSON-RPC surface needed to emit ``AcpRpcEffect`` values."""

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


class AcpEffectTransport:
    """Dispatch ACP RPC effects; reject keystroke and app-server effects."""

    def __init__(self, connection: AcpRpcPort) -> None:
        self._connection = connection

    async def send_literal_keys(self, text: str, *, inter_key_delay: DelayProfile | None) -> None:
        raise TypeError(
            "ACP transport does not accept keystroke effects; "
            "adapters must lower them to AcpRpcEffect"
        )

    async def paste_buffer(self, text: str) -> None:
        raise TypeError(
            "ACP transport does not accept keystroke effects; "
            "adapters must lower them to AcpRpcEffect"
        )

    async def send_named_key(self, key: str) -> None:
        raise TypeError(
            "ACP transport does not accept keystroke effects; "
            "adapters must lower them to AcpRpcEffect"
        )

    async def invoke_app_server_rpc(self, effect: AppServerRpcEffect) -> None:
        raise TypeError("ACP transport cannot invoke app-server RPC")

    async def invoke_agent_sdk(self, effect: AgentSdkEffect) -> None:
        raise TypeError("ACP transport cannot invoke Agent SDK effects")

    async def invoke_acp_rpc(self, effect: AcpRpcEffect) -> None:
        if effect.response_id is not None:
            await self._connection.respond(
                effect.response_id,
                result=effect.response_result,
                error=effect.response_error,
            )
            return
        if effect.expects_response:
            is_prompt = effect.method == "session/prompt"
            if is_prompt and hasattr(self._connection, "prompt_in_flight"):
                self._connection.prompt_in_flight = True
            try:
                await self._connection.request(effect.method, effect.params)
            finally:
                if is_prompt and hasattr(self._connection, "prompt_in_flight"):
                    self._connection.prompt_in_flight = False
            return
        await self._connection.notify(effect.method, effect.params)
        # session/cancel has no agent reply; signal the frame observer to end the turn.
        if effect.method == "session/cancel":
            self._connection.pending_stop_reason = "cancelled"  # type: ignore[attr-defined]


__all__ = [
    "AcpEffectTransport",
    "AcpRpcPort",
]
