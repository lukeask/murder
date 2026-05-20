"""OpenRouter HTTP client (OAI-compatible)."""

from __future__ import annotations

import os

from murder.clients.chat_completions import ChatCompletionsClient

OPENROUTER_BASE = "https://openrouter.ai/api/v1"


class OpenRouterClient(ChatCompletionsClient):
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = OPENROUTER_BASE,
        http_referer: str = "https://github.com/lukeask/murder",
        app_title: str = "murder",
    ) -> None:
        key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is unset")
        super().__init__(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {key}",
                "HTTP-Referer": http_referer,
                "X-Title": app_title,
                "Content-Type": "application/json",
            },
        )
