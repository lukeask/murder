"""Native LLM clients for CrowHandler and PlanningAgent.

Default inference uses Groq/Cerebras (``AutoFreeClient``). OpenRouter, Anthropic,
and OpenAI are opt-in only — selected explicitly via config tiers or roles.
"""

import logging
import os
from typing import TYPE_CHECKING

from murder.llm.clients.auto_free import AutoFreeClient
from murder.llm.clients.anthropic import AnthropicClient
from murder.llm.clients.base import APIClient
from murder.llm.clients.cerebras import CerebrasClient
from murder.llm.clients.groq import GroqClient
from murder.llm.clients.openai_compatible import OPENAI_BASE, OpenAICompatibleClient
from murder.config import ApiRoleConfig
from murder.llm.clients.openrouter import OpenRouterClient

if TYPE_CHECKING:
    from murder.user_config import UserConfig

LOGGER = logging.getLogger(__name__)


def create_client(provider: str) -> APIClient | None:
    """Create the configured API client, or None when required env is absent."""
    try:
        if provider == "groq":
            return GroqClient()
        if provider == "cerebras":
            return CerebrasClient()
        if provider == "anthropic":
            return AnthropicClient()
        if provider == "openai":
            return OpenAICompatibleClient(base_url=os.environ.get("OPENAI_BASE_URL", OPENAI_BASE))
        if provider == "local":
            base_url = os.environ.get("LOCAL_OPENAI_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
            if not base_url:
                return None
            return OpenAICompatibleClient(
                api_key=os.environ.get("LOCAL_OPENAI_API_KEY", ""),
                base_url=base_url,
                require_api_key=False,
            )
        if provider == "openrouter":
            return OpenRouterClient()
    except RuntimeError:
        return None
    raise ValueError(f"unknown API provider: {provider}")


def resolve_role_client(config: ApiRoleConfig) -> APIClient | None:
    if config.auto_free:
        return AutoFreeClient.build_default()
    return create_client(config.provider)


def resolve_role_client_tiered(
    config: ApiRoleConfig,
    user_cfg: "UserConfig | None",
    role: str,
) -> tuple[APIClient | None, ApiRoleConfig]:
    """Resolve a role's client honoring user-config tier overrides.

    Returns ``(client, effective_config)``. When the role maps to a tier, the
    effective config carries the tier's provider/model/auto_free so the right
    model string reaches API calls. Any tier failure degrades to today's
    behavior, returning the ORIGINAL config + original-path client.
    """
    from murder.user_config import resolve_tier

    tier = resolve_tier(user_cfg, role)
    if tier is None:
        return (resolve_role_client(config), config)
    if tier.auto_free and tier.model:
        LOGGER.warning(
            "tier %r sets both auto_free and model=%r; the model is ignored "
            "(auto-free uses its fixed free pool)",
            role,
            tier.model,
        )
    effective_cfg = config.model_copy(
        update={
            "provider": tier.provider,
            "model": tier.model,
            "auto_free": tier.auto_free,
        }
    )
    client = resolve_role_client(effective_cfg)
    if client is None:
        return (resolve_role_client(config), config)
    return (client, effective_cfg)


__all__ = [
    "APIClient",
    "AutoFreeClient",
    "AnthropicClient",
    "CerebrasClient",
    "GroqClient",
    "OpenAICompatibleClient",
    "OpenRouterClient",
    "create_client",
    "resolve_role_client",
    "resolve_role_client_tiered",
]
