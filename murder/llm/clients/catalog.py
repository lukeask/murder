"""Built-in direct-inference provider definitions.

Provider definitions describe static adapter behavior. Persisted provider
instances remain owned by :mod:`murder.user_config`, and policy selection stays
client-free in :mod:`murder.llm.policy`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import httpx

from murder.llm.clients.anthropic import ANTHROPIC_BASE, AnthropicClient
from murder.llm.clients.base import APIClient
from murder.llm.clients.cerebras import CEREBRAS_BASE, CerebrasClient
from murder.llm.clients.groq import GROQ_BASE, GroqClient
from murder.llm.clients.openai_compatible import OPENAI_BASE, OpenAICompatibleClient
from murder.llm.clients.openrouter import OPENROUTER_BASE, OpenRouterClient
from murder.llm.policy import CandidateMetadata

if TYPE_CHECKING:
    from murder.user_config import UserLlmProviderSettings

ProviderType = Literal[
    "groq",
    "cerebras",
    "openrouter",
    "openai",
    "anthropic",
    "lemonade",
    "openai_compatible",
]
FieldKind = Literal["secret", "url"]
LEMONADE_BASE = "http://localhost:8000"


@dataclass(frozen=True)
class ProviderFieldSpec:
    """A provider setup field which a settings surface may render."""

    name: str
    label: str
    kind: FieldKind
    required: bool
    secret: bool = False


@dataclass(frozen=True)
class ModelPreset:
    """A curated model and metadata supplied by a provider definition."""

    id: str
    label: str
    metadata: CandidateMetadata = CandidateMetadata()


ClientFactory = Callable[[str | None, str | None], APIClient]
EndpointNormalizer = Callable[[str], str]


@dataclass(frozen=True)
class ProviderDefinition:
    """Static adapter contract for one supported provider type."""

    type: ProviderType
    label: str
    default_endpoint: str | None
    field_specs: tuple[ProviderFieldSpec, ...]
    metadata: CandidateMetadata
    presets: tuple[ModelPreset, ...] = ()
    canonical_instance: bool = True
    multiple_instances: bool = False
    discovery_path: str | None = None
    _factory: ClientFactory = field(repr=False, compare=False, default=lambda _key, _url: None)  # type: ignore[assignment]
    _endpoint_normalizer: EndpointNormalizer = field(
        repr=False, compare=False, default=lambda endpoint: endpoint.rstrip("/")
    )

    @property
    def requires_api_key(self) -> bool:
        return any(spec.name == "api_key" and spec.required for spec in self.field_specs)

    @property
    def supports_discovery(self) -> bool:
        return self.discovery_path is not None

    def recommended_catalog(self) -> dict[str, CandidateMetadata]:
        """Return a fresh catalog suitable for ``DirectLlmResolver`` input."""
        return {
            preset.id: _merge_metadata(self.metadata, preset.metadata) for preset in self.presets
        }

    def create_client(self, provider: UserLlmProviderSettings) -> APIClient | None:
        """Build a client from a persisted instance, degrading on missing setup."""
        try:
            endpoint = provider.endpoint or self.default_endpoint
            return self._factory(
                provider.auth.api_key,
                self._endpoint_normalizer(endpoint) if endpoint else None,
            )
        except RuntimeError:
            return None

    async def discover_models(
        self,
        provider: UserLlmProviderSettings,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> dict[str, CandidateMetadata]:
        """Discover OpenAI-style models, normalized to the policy catalog shape.

        Discovery deliberately does not mutate the persisted model source or
        resolver. Callers decide whether and how to cache the returned catalog.
        """
        if self.discovery_path is None:
            raise ValueError(f"provider {self.type} does not support model discovery")
        endpoint = provider.endpoint or self.default_endpoint
        if not endpoint:
            raise ValueError(f"provider {self.type} requires an endpoint for discovery")
        endpoint = self._endpoint_normalizer(endpoint)
        headers = {"Accept": "application/json"}
        if provider.auth.api_key:
            headers["Authorization"] = f"Bearer {provider.auth.api_key}"
        owns_client = http_client is None
        client = http_client or httpx.AsyncClient(timeout=20.0, headers=headers)
        try:
            response = await client.get(f"{endpoint.rstrip('/')}{self.discovery_path}")
            response.raise_for_status()
            payload = response.json()
        finally:
            if owns_client:
                await client.aclose()
        entries = payload.get("data", []) if isinstance(payload, Mapping) else []
        if not isinstance(entries, list):
            raise ValueError("provider model discovery response has a non-list data field")
        return {
            model_id: self.metadata
            for entry in entries
            if isinstance(entry, Mapping)
            and isinstance((model_id := entry.get("id")), str)
            and model_id
        }


def _merge_metadata(base: CandidateMetadata, override: CandidateMetadata) -> CandidateMetadata:
    """Keep provider defaults while allowing a preset to supply richer fields."""
    return CandidateMetadata(
        locality=override.locality if override.locality != "unknown" else base.locality,
        cost_class=override.cost_class if override.cost_class != "unknown" else base.cost_class,
        tags=base.tags | override.tags,
        capabilities=base.capabilities | override.capabilities,
        execution_modes=base.execution_modes | override.execution_modes,
        context_window=override.context_window or base.context_window,
    )


_REMOTE_FREE = CandidateMetadata(
    locality="remote",
    cost_class="free",
    capabilities=frozenset({"tools"}),
    execution_modes=frozenset({"immediate"}),
)
_REMOTE_PAID = CandidateMetadata(
    locality="remote",
    cost_class="paid",
    capabilities=frozenset({"tools"}),
    execution_modes=frozenset({"immediate"}),
)
_REMOTE_UNKNOWN = CandidateMetadata(
    locality="remote",
    capabilities=frozenset({"tools"}),
    execution_modes=frozenset({"immediate"}),
)
_LOCAL = CandidateMetadata(
    locality="local",
    capabilities=frozenset({"tools"}),
    execution_modes=frozenset({"immediate"}),
)
_API_KEY = ProviderFieldSpec("api_key", "API key", "secret", required=True, secret=True)
_OPTIONAL_API_KEY = ProviderFieldSpec("api_key", "API key", "secret", required=False, secret=True)
_ENDPOINT = ProviderFieldSpec("endpoint", "Endpoint", "url", required=True)


def _groq_factory(api_key: str | None, endpoint: str | None) -> APIClient:
    return GroqClient(api_key=api_key, base_url=endpoint or GROQ_BASE)


def _cerebras_factory(api_key: str | None, endpoint: str | None) -> APIClient:
    return CerebrasClient(api_key=api_key, base_url=endpoint or CEREBRAS_BASE)


def _openrouter_factory(api_key: str | None, endpoint: str | None) -> APIClient:
    return OpenRouterClient(api_key=api_key, base_url=endpoint or OPENROUTER_BASE)


def _openai_factory(api_key: str | None, endpoint: str | None) -> APIClient:
    return OpenAICompatibleClient(api_key=api_key, base_url=endpoint or OPENAI_BASE)


def _anthropic_factory(api_key: str | None, endpoint: str | None) -> APIClient:
    return AnthropicClient(api_key=api_key, base_url=endpoint or ANTHROPIC_BASE)


def _compatible_factory(api_key: str | None, endpoint: str | None) -> APIClient:
    if not endpoint:
        raise RuntimeError("an OpenAI-compatible provider requires an endpoint")
    return OpenAICompatibleClient(api_key=api_key or "", base_url=endpoint, require_api_key=False)


def _lemonade_endpoint(endpoint: str) -> str:
    """Normalize a Lemonade server root to its OpenAI-compatible API base."""
    clean = endpoint.rstrip("/")
    return clean if clean.endswith("/api/v1") else f"{clean}/api/v1"


PROVIDER_DEFINITIONS: dict[ProviderType, ProviderDefinition] = {
    "groq": ProviderDefinition(
        "groq",
        "Groq",
        GROQ_BASE,
        (_API_KEY,),
        _REMOTE_FREE,
        presets=(
            ModelPreset(
                "qwen/qwen3-32b", "Qwen3 32B", CandidateMetadata(tags=frozenset({"reasoning"}))
            ),
            ModelPreset(
                "openai/gpt-oss-120b",
                "GPT-OSS 120B",
                CandidateMetadata(tags=frozenset({"reasoning"})),
            ),
            ModelPreset("llama-3.3-70b-versatile", "Llama 3.3 70B"),
        ),
        _factory=_groq_factory,
    ),
    "cerebras": ProviderDefinition(
        "cerebras",
        "Cerebras",
        CEREBRAS_BASE,
        (_API_KEY,),
        _REMOTE_FREE,
        presets=(
            ModelPreset("zai-glm-4.7", "GLM 4.7", CandidateMetadata(tags=frozenset({"reasoning"}))),
            ModelPreset(
                "gpt-oss-120b", "GPT-OSS 120B", CandidateMetadata(tags=frozenset({"reasoning"}))
            ),
        ),
        _factory=_cerebras_factory,
    ),
    "openrouter": ProviderDefinition(
        "openrouter",
        "OpenRouter",
        OPENROUTER_BASE,
        (_API_KEY,),
        _REMOTE_UNKNOWN,
        presets=(ModelPreset("openai/gpt-4o-mini", "GPT-4o mini"),),
        discovery_path="/models",
        _factory=_openrouter_factory,
    ),
    "openai": ProviderDefinition(
        "openai",
        "OpenAI",
        OPENAI_BASE,
        (_API_KEY,),
        _REMOTE_PAID,
        presets=(ModelPreset("gpt-4.1-mini", "GPT-4.1 mini"),),
        discovery_path="/models",
        _factory=_openai_factory,
    ),
    "anthropic": ProviderDefinition(
        "anthropic",
        "Anthropic",
        ANTHROPIC_BASE,
        (_API_KEY,),
        _REMOTE_PAID,
        presets=(ModelPreset("claude-sonnet-4-20250514", "Claude Sonnet 4"),),
        _factory=_anthropic_factory,
    ),
    "lemonade": ProviderDefinition(
        "lemonade",
        "Lemonade",
        LEMONADE_BASE,
        (_OPTIONAL_API_KEY, _ENDPOINT),
        _LOCAL,
        canonical_instance=False,
        multiple_instances=True,
        discovery_path="/models",
        _factory=_compatible_factory,
        _endpoint_normalizer=_lemonade_endpoint,
    ),
    "openai_compatible": ProviderDefinition(
        "openai_compatible",
        "OpenAI-compatible",
        None,
        (_OPTIONAL_API_KEY, _ENDPOINT),
        _LOCAL,
        canonical_instance=False,
        multiple_instances=True,
        discovery_path="/models",
        _factory=_compatible_factory,
    ),
}

# ``local`` is the legacy role/tier spelling for an OpenAI-compatible instance.
_TYPE_ALIASES = {"local": "openai_compatible"}


def get_provider_definition(provider_type: str) -> ProviderDefinition:
    """Return a registered definition, including the legacy ``local`` alias."""
    resolved_type = _TYPE_ALIASES.get(provider_type, provider_type)
    try:
        return PROVIDER_DEFINITIONS[resolved_type]  # type: ignore[index]
    except KeyError as exc:
        raise ValueError(f"unknown API provider type: {provider_type}") from exc


def create_instance_client(provider_id: str, provider: UserLlmProviderSettings) -> APIClient | None:
    """Construct a client for a persisted provider instance or return ``None``.

    The policy resolver may call this only after it has selected a candidate.
    ``None`` represents missing credentials or an invalid local endpoint, not a
    network failure from a previously constructed client.
    """
    return get_provider_definition(provider.type or provider_id).create_client(provider)


def recommended_catalogs() -> dict[str, dict[str, CandidateMetadata]]:
    """Return all static catalogs in ``DirectLlmResolver`` input shape."""
    return {
        kind: definition.recommended_catalog() for kind, definition in PROVIDER_DEFINITIONS.items()
    }


__all__ = [
    "LEMONADE_BASE",
    "ModelPreset",
    "PROVIDER_DEFINITIONS",
    "ProviderDefinition",
    "ProviderFieldSpec",
    "ProviderType",
    "create_instance_client",
    "get_provider_definition",
    "recommended_catalogs",
]
