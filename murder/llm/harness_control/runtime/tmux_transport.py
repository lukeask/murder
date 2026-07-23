"""tmux implementation of the actuator's physical-effect transport.

No controller or adapter imports this module. It is the intentionally small
place where a verified operation becomes a tmux call; acceptance here remains
an emission result, never a verified harness outcome.
"""

from __future__ import annotations

import asyncio
import random

from murder.llm.harness_control.model.actions import (
    AcpRpcEffect,
    AgentSdkEffect,
    AppServerRpcEffect,
    DelayProfile,
)
from murder.runtime.terminal import tmux


class TmuxTerminalEffectTransport:
    def __init__(self, session: str, *, rng: random.Random | None = None) -> None:
        self._session = session
        self._rng = rng or random.Random()

    async def send_literal_keys(self, text: str, *, inter_key_delay: DelayProfile | None) -> None:
        if inter_key_delay is None:
            await tmux.send_keys(self._session, text, literal=True, enter=False)
            return
        for index, character in enumerate(text):
            await tmux.send_keys(self._session, character, literal=True, enter=False)
            if index != len(text) - 1:
                await asyncio.sleep(self._delay_seconds(inter_key_delay))

    async def paste_buffer(self, text: str) -> None:
        await tmux.paste_buffer_literal(self._session, text)

    async def send_named_key(self, key: str) -> None:
        await tmux.send_keys(self._session, key, literal=False, enter=False)

    async def invoke_app_server_rpc(self, effect: AppServerRpcEffect) -> None:
        raise TypeError("tmux transport cannot invoke app-server RPC")

    async def invoke_agent_sdk(self, effect: AgentSdkEffect) -> None:
        raise TypeError("tmux transport cannot invoke Agent SDK effects")

    async def invoke_acp_rpc(self, effect: AcpRpcEffect) -> None:
        raise TypeError("tmux transport cannot invoke ACP RPC")

    def _delay_seconds(self, profile: DelayProfile) -> float:
        low, high = profile.min_delay_ms, profile.max_delay_ms
        if profile.distribution == "uniform":
            milliseconds = self._rng.uniform(low, high)
        else:
            milliseconds = self._rng.triangular(low, high, (low + high) / 2)
        return milliseconds / 1000
