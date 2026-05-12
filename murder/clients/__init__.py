"""Native LLM clients for CrowHandler and Sentinel."""

import os

from murder.clients.anthropic import AnthropicClient
from murder.clients.base import APIClient
from murder.clients.openai_compatible import OPENAI_BASE, OpenAICompatibleClient
from murder.clients.openrouter import OpenRouterClient


def create_client(provider: str) -> APIClient | None:
    """Create the configured API client, or None when required env is absent."""
    try:
        if provider == "openrouter":
            return OpenRouterClient()
        if provider == "anthropic":
            return AnthropicClient()
        if provider == "openai":
            return OpenAICompatibleClient(base_url=os.environ.get("OPENAI_BASE_URL", OPENAI_BASE))
        if provider == "local":
            base_url = os.environ.get("LOCAL_OPENAI_BASE_URL") or os.environ.get(
                "OPENAI_BASE_URL"
            )
            if not base_url:
                return None
            return OpenAICompatibleClient(
                api_key=os.environ.get("LOCAL_OPENAI_API_KEY", ""),
                base_url=base_url,
                require_api_key=False,
            )
    except RuntimeError:
        return None
    raise ValueError(f"unknown API provider: {provider}")


__all__ = [
    "APIClient",
    "AnthropicClient",
    "OpenAICompatibleClient",
    "OpenRouterClient",
    "create_client",
]
