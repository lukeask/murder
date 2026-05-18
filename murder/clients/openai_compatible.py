"""OpenAI-compatible chat completions client."""

from __future__ import annotations

import os

from murder.clients.openrouter import OpenRouterClient

OPENAI_BASE = "https://api.openai.com/v1"


class OpenAICompatibleClient(OpenRouterClient):
    """Client for OpenAI-compatible `/chat/completions` providers.

    This covers first-party OpenAI and local OpenAI-compatible endpoints. Local
    endpoints may omit API keys; public endpoints generally should not.
    """

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
        self.api_key = key
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL") or OPENAI_BASE).rstrip("/")
        self._headers = {"Content-Type": "application/json"}
        if self.api_key:
            self._headers["Authorization"] = f"Bearer {self.api_key}"
        self._client = None
