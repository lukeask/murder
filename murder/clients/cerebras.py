"""Cerebras inference (OpenAI-compatible)."""

from __future__ import annotations

import os

from murder.clients.openai_compatible import OpenAICompatibleClient

CEREBRAS_BASE = "https://api.cerebras.ai/v1"


class CerebrasClient(OpenAICompatibleClient):
    def __init__(self, api_key: str | None = None) -> None:
        key = api_key or os.environ.get("CEREBRAS_API_KEY", "")
        if not key:
            raise RuntimeError("CEREBRAS_API_KEY is unset")
        super().__init__(api_key=key, base_url=CEREBRAS_BASE, api_key_env="CEREBRAS_API_KEY")
