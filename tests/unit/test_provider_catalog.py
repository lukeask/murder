"""Provider definition registry and instance-aware native clients."""

from __future__ import annotations

import httpx

from murder.llm.clients.catalog import (
    LEMONADE_BASE,
    PROVIDER_DEFINITIONS,
    create_instance_client,
    get_provider_definition,
    recommended_catalogs,
)
from murder.llm.clients.openai_compatible import OpenAICompatibleClient
from murder.user_config import UserLlmProviderSettings


def _provider(kind: str, **kwargs: object) -> UserLlmProviderSettings:
    return UserLlmProviderSettings(type=kind, **kwargs)


def test_registry_covers_required_provider_integrations() -> None:
    assert set(PROVIDER_DEFINITIONS) == {
        "groq",
        "cerebras",
        "openrouter",
        "openai",
        "anthropic",
        "lemonade",
        "openai_compatible",
    }
    assert PROVIDER_DEFINITIONS["openai_compatible"].multiple_instances
    assert PROVIDER_DEFINITIONS["lemonade"].multiple_instances
    assert not PROVIDER_DEFINITIONS["groq"].multiple_instances
    assert PROVIDER_DEFINITIONS["groq"].requires_api_key


def test_recommended_catalogs_provide_resolver_metadata() -> None:
    catalog = recommended_catalogs()

    groq_model = catalog["groq"]["qwen/qwen3-32b"]
    assert groq_model.locality == "remote"
    assert groq_model.cost_class == "free"
    assert "tools" in groq_model.capabilities
    assert "reasoning" in groq_model.tags


def test_compatible_instance_uses_persisted_endpoint_without_key() -> None:
    client = create_instance_client(
        "home-vllm", _provider("openai_compatible", endpoint="http://vllm.example/v1")
    )

    assert isinstance(client, OpenAICompatibleClient)
    assert client.base_url == "http://vllm.example/v1"
    assert "Authorization" not in client._headers


def test_lemonade_instance_uses_definition_default_endpoint() -> None:
    client = create_instance_client("laptop-lemonade", _provider("lemonade"))

    assert isinstance(client, OpenAICompatibleClient)
    assert client.base_url == f"{LEMONADE_BASE}/api/v1"


def test_missing_required_credentials_degrade_to_no_client(monkeypatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    assert create_instance_client("groq", _provider("groq")) is None


async def test_compatible_discovery_normalizes_openai_models() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, json={"data": [{"id": "local-qwen"}, {"id": "local-llama"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        models = await get_provider_definition("openai_compatible").discover_models(
            _provider("openai_compatible", endpoint="http://local.test/v1"), http_client=client
        )

    assert requested == ["http://local.test/v1/models"]
    assert set(models) == {"local-qwen", "local-llama"}
    assert all(metadata.locality == "local" for metadata in models.values())


async def test_discovery_rejects_unsupported_provider() -> None:
    try:
        await get_provider_definition("anthropic").discover_models(_provider("anthropic"))
    except ValueError as exc:
        assert "does not support model discovery" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("unsupported discovery was accepted")
