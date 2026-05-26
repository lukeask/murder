"""Groq inference (OpenAI-compatible)."""

from __future__ import annotations

import os

from murder.clients.openai_compatible import OpenAICompatibleClient

GROQ_BASE = "https://api.groq.com/openai/v1"


class GroqClient(OpenAICompatibleClient):
    def __init__(self, api_key: str | None = None) -> None:
        key = api_key or os.environ.get("GROQ_API_KEY", "")
        if not key:
            raise RuntimeError("GROQ_API_KEY is unset")
        super().__init__(api_key=key, base_url=GROQ_BASE, api_key_env="GROQ_API_KEY")
