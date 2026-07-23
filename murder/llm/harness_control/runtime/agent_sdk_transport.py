"""Claude Agent SDK implementation of the actuator's effect transport."""

from __future__ import annotations

from typing import Protocol

from murder.llm.harness_control.model.actions import (
    AcpRpcEffect,
    AgentSdkEffect,
    AppServerRpcEffect,
    DelayProfile,
)


class AgentSdkPort(Protocol):
    """Minimal surface needed to emit ``AgentSdkEffect`` values."""

    async def query(self, prompt: str) -> None: ...

    async def interrupt(self) -> None: ...

    async def set_model(self, model: str | None) -> None: ...

    async def respond_permission(
        self,
        request_id: str,
        *,
        behavior: str,
        updated_input: dict[str, object] | None = None,
        message: str = "",
        interrupt: bool = False,
    ) -> None: ...


class AgentSdkEffectTransport:
    """Dispatch Agent SDK effects; reject keystroke effects."""

    def __init__(self, connection: AgentSdkPort) -> None:
        self._connection = connection

    async def send_literal_keys(self, text: str, *, inter_key_delay: DelayProfile | None) -> None:
        raise TypeError(
            "agent-sdk transport does not accept keystroke effects; "
            "adapters must lower them to AgentSdkEffect"
        )

    async def paste_buffer(self, text: str) -> None:
        raise TypeError(
            "agent-sdk transport does not accept keystroke effects; "
            "adapters must lower them to AgentSdkEffect"
        )

    async def send_named_key(self, key: str) -> None:
        raise TypeError(
            "agent-sdk transport does not accept keystroke effects; "
            "adapters must lower them to AgentSdkEffect"
        )

    async def invoke_app_server_rpc(self, effect: AppServerRpcEffect) -> None:
        raise TypeError("agent-sdk transport cannot invoke app-server RPC")

    async def invoke_acp_rpc(self, effect: AcpRpcEffect) -> None:
        raise TypeError("agent-sdk transport cannot invoke ACP RPC")

    async def invoke_agent_sdk(self, effect: AgentSdkEffect) -> None:
        if effect.op == "query":
            params = effect.params or {}
            prompt = params.get("prompt")
            if not isinstance(prompt, str):
                raise ValueError("AgentSdkEffect query requires params.prompt string")
            await self._connection.query(prompt)
            return
        if effect.op == "interrupt":
            await self._connection.interrupt()
            return
        if effect.op == "set_model":
            params = effect.params or {}
            model = params.get("model")
            await self._connection.set_model(str(model) if model is not None else None)
            return
        if effect.op == "respond_permission":
            if effect.request_id is None:
                raise ValueError("AgentSdkEffect respond_permission requires request_id")
            behavior = effect.permission_behavior or "allow"
            await self._connection.respond_permission(
                effect.request_id,
                behavior=behavior,
                updated_input=effect.updated_input,
                message=effect.message or "",
                interrupt=effect.interrupt,
            )
            return
        raise ValueError(f"unsupported AgentSdkEffect op {effect.op!r}")


__all__ = [
    "AgentSdkEffectTransport",
    "AgentSdkPort",
]
