from __future__ import annotations

import pytest

from murder.clients import AnthropicClient, OpenAICompatibleClient, create_client


def test_create_openai_client_from_env(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    client = create_client("openai")
    assert isinstance(client, OpenAICompatibleClient)
    assert client.base_url == "https://api.openai.com/v1"


def test_create_local_client_without_api_key(monkeypatch) -> None:
    monkeypatch.delenv("LOCAL_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("LOCAL_OPENAI_BASE_URL", "http://inferno:8080/v1")
    client = create_client("local")
    assert isinstance(client, OpenAICompatibleClient)
    assert client.base_url == "http://inferno:8080/v1"
    assert client.api_key == ""


def test_local_client_without_base_url_is_unavailable(monkeypatch) -> None:
    monkeypatch.delenv("LOCAL_OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    assert create_client("local") is None


def test_create_anthropic_client_from_env(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert isinstance(create_client("anthropic"), AnthropicClient)


def test_unknown_provider_fails_loud() -> None:
    with pytest.raises(ValueError):
        create_client("bogus")
