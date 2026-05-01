"""Native LLM clients for Augur and Sentinel (D2-orthogonal — these don't
wrap an interactive CLI). v0 ships an OpenRouter-compatible client; the
ABC in `base.py` is provider-agnostic so Anthropic / local / OAI drop in
later.
"""

from murder.clients.base import APIClient
from murder.clients.openrouter import OpenRouterClient

__all__ = ["APIClient", "OpenRouterClient"]
