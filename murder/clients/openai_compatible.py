"""OpenAI-compatible chat completions client."""

from __future__ import annotations

import os

from murder.clients.chat_completions import ChatCompletionsClient

OPENAI_BASE = "https://api.openai.com/v1"


class OpenAICompatibleClient(ChatCompletionsClient):
    """Client for OpenAI-compatible ``/chat/completions`` providers."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        require_api_key: bool = True,
    ) -> None:
        key = api_key if api_key is not None else os.environ.get(api_key_env, "")
        if require_api_key and not key:
            raise RuntimeError(f"{api_key_env} is unset")
        resolved_base = (base_url or os.environ.get("OPENAI_BASE_URL") or OPENAI_BASE).rstrip("/")
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        super().__init__(base_url=resolved_base, headers=headers)
