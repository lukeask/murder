"""Failover client for free-tier inference providers."""

from __future__ import annotations

import logging
from typing import Any

from murder.llm.clients.base import APIClient, CompletionResult, ToolSpec

LOGGER = logging.getLogger(__name__)

_DEFAULT_POOL = [
    ("groq", "openai/gpt-oss-120b"),
    ("cerebras", "openai/gpt-oss-120b"),
]


class AutoFreeClient(APIClient):
    """Try a pool of provider/model pairs until one succeeds."""

    def __init__(self, pool: list[tuple[APIClient, str]]) -> None:
        self.pool = pool

    async def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> CompletionResult:
        # The free pool is fixed (groq/cerebras gpt-oss); the caller's requested
        # model is intentionally discarded. resolve_role_client_tiered may have
        # copied a tier model into the effective config, but auto_free=True means
        # that model is never used — the pool entries win.
        if model:
            LOGGER.debug("auto-free ignoring requested model %r; using fixed pool", model)
        for entry_client, entry_model in self.pool:
            try:
                return await entry_client.complete(
                    model=entry_model,
                    system=system,
                    messages=messages,
                    tools=tools,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    **kwargs,
                )
            except Exception:
                LOGGER.exception("auto-free provider failed", extra={"model": entry_model})
                continue
        raise RuntimeError("all auto-free providers failed")

    @classmethod
    def build_default(cls) -> AutoFreeClient | None:
        from murder.llm.clients import create_client

        entries: list[tuple[APIClient, str]] = []
        for provider, model in _DEFAULT_POOL:
            client = create_client(provider)
            if client is None:
                continue
            entries.append((client, model))
        if not entries:
            return None
        return cls(entries)
