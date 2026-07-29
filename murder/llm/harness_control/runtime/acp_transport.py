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


def _prompt_text(params: dict[str, object] | None) -> str | None:
    if params is None:
        return None
    blocks = params.get("prompt")
    if not isinstance(blocks, list):
        return None
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts) if parts else None


def _stash_prompt_stop_reason(connection: AcpRpcPort, result: object) -> None:
    stop_reason = "end_turn"
    if isinstance(result, dict):
        raw = result.get("stopReason")
        if isinstance(raw, str) and raw:
            stop_reason = raw
    connection.pending_stop_reason = stop_reason  # type: ignore[attr-defined]


def _stash_config_options(connection: AcpRpcPort, result: object) -> None:
    if not isinstance(result, dict):
        return
    options = result.get("configOptions")
    if not isinstance(options, list):
        return
    catalog = [option for option in options if isinstance(option, dict)]
    connection.pending_config_options = catalog  # type: ignore[attr-defined]
    connection.session_config_options = catalog  # type: ignore[attr-defined]


async def _apply_desired_model_params(connection: AcpRpcPort) -> None:
    """Apply staged fast/effort after a model config write using the live catalog."""
    from murder.llm.harness_control.adapters.rpc_model_options import (  # noqa: PLC0415
        plan_acp_model_config_writes,
    )

    session_id = getattr(connection, "session_id", None)
    if not isinstance(session_id, str) or not session_id:
        return
    catalog = list(getattr(connection, "session_config_options", []) or [])
    desired_fast = getattr(connection, "desired_fast_enabled", None)
    desired_effort = getattr(connection, "desired_effort", None)
    # Model write is owned by the lowered SelectModel effect; only finish params.
    writes = plan_acp_model_config_writes(
        catalog,
        model_id=None,
        fast_enabled=desired_fast if isinstance(desired_fast, bool) else None,
        effort=desired_effort if isinstance(desired_effort, str) else None,
    )
    for config_id, value in writes:
        result = await connection.request(
            "session/set_config_option",
            {
                "sessionId": session_id,
                "configId": config_id,
                "value": value,
            },
        )
        _stash_config_options(connection, result)


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
            prompt_text = _prompt_text(effect.params) if is_prompt else None
            if is_prompt and hasattr(self._connection, "prompt_in_flight"):
                self._connection.prompt_in_flight = True
            if is_prompt and hasattr(self._connection, "pending_prompt_text"):
                self._connection.pending_prompt_text = prompt_text
            try:
                result = await self._connection.request(effect.method, effect.params)
            except BaseException:
                if (
                    is_prompt
                    and hasattr(self._connection, "pending_prompt_text")
                    and self._connection.pending_prompt_text == prompt_text
                ):
                    self._connection.pending_prompt_text = None
                raise
            finally:
                if is_prompt and hasattr(self._connection, "prompt_in_flight"):
                    self._connection.prompt_in_flight = False
            # session/prompt's result carries stopReason when the turn ends.
            # Stash it for AcpFrameObserver so turn_status leaves "streaming"
            # (otherwise the crow stays working/running forever after SUCCEEDED).
            if is_prompt:
                _stash_prompt_stop_reason(self._connection, result)
            elif effect.method == "session/set_config_option":
                _stash_config_options(self._connection, result)
                # After a model/config write, finish staged fast/effort against
                # the refreshed parameterized catalog (Composer slow/fast, etc.).
                await _apply_desired_model_params(self._connection)
            return
        await self._connection.notify(effect.method, effect.params)
        # session/cancel has no agent reply; signal the frame observer to end the turn.
        if effect.method == "session/cancel":
            self._connection.pending_stop_reason = "cancelled"  # type: ignore[attr-defined]


__all__ = [
    "AcpEffectTransport",
    "AcpRpcPort",
]
