"""Unit tests for RT4 model tiering: tiered role-client resolver + Runtime seam."""

from __future__ import annotations

from pathlib import Path

from murder.config import (
    ApiRoleConfig,
    Config,
    CrowHandlerConfig,
    HarnessRoleConfig,
    ProjectConfig,
)
from murder.llm.clients import resolve_role_client, resolve_role_client_tiered
from murder.user_config import UserConfig, UserLlmConfig, UserLlmTier


class _FakeClient:
    """Stand-in for an APIClient so no network/credentials are touched."""


def _user_cfg(role: str, tier: UserLlmTier, tier_name: str = "t") -> UserConfig:
    return UserConfig(
        llm=UserLlmConfig(tiers={tier_name: tier}, roles={role: tier_name})
    )


def test_no_user_cfg_returns_original_config_object() -> None:
    config = ApiRoleConfig(provider="cerebras", model="base-model", auto_free=False)
    sentinel = _FakeClient()

    def fake_resolve(cfg: ApiRoleConfig) -> object:
        return sentinel

    import murder.llm.clients as clients_mod

    orig = clients_mod.resolve_role_client
    clients_mod.resolve_role_client = fake_resolve  # type: ignore[assignment]
    try:
        client, eff = resolve_role_client_tiered(config, None, "notetaker")
    finally:
        clients_mod.resolve_role_client = orig  # type: ignore[assignment]

    assert client is sentinel
    assert eff is config


def test_tier_applies_provider_model_auto_free(monkeypatch) -> None:
    config = ApiRoleConfig(provider="cerebras", model="base-model", auto_free=False)
    tier = UserLlmTier(provider="groq", model="tier-model", auto_free=True)
    user_cfg = _user_cfg("notetaker", tier)

    built: dict[str, object] = {}

    def fake_build_default() -> object:
        c = _FakeClient()
        built["client"] = c
        return c

    # auto_free=True routes through AutoFreeClient.build_default
    monkeypatch.setattr(
        "murder.llm.clients.AutoFreeClient.build_default", staticmethod(fake_build_default)
    )

    client, eff = resolve_role_client_tiered(config, user_cfg, "notetaker")

    assert client is built["client"]
    assert eff is not config
    assert eff.provider == "groq"
    assert eff.model == "tier-model"
    assert eff.auto_free is True


def test_tier_build_failure_falls_back_to_original(monkeypatch) -> None:
    config = ApiRoleConfig(provider="cerebras", model="base-model", auto_free=False)
    tier = UserLlmTier(provider="groq", model="tier-model", auto_free=False)
    user_cfg = _user_cfg("crow_handler", tier)

    fallback_client = _FakeClient()

    def fake_create_client(provider: str) -> object | None:
        # tier path uses provider "groq" -> fail; original uses "cerebras" -> ok
        if provider == "groq":
            return None
        return fallback_client

    monkeypatch.setattr("murder.llm.clients.create_client", fake_create_client)

    client, eff = resolve_role_client_tiered(config, user_cfg, "crow_handler")

    assert client is fallback_client
    assert eff is config


def test_subclass_preservation(monkeypatch) -> None:
    config = CrowHandlerConfig(provider="cerebras", model="base-model", poll_interval_s=12.5, auto_free=False)
    tier = UserLlmTier(provider="anthropic", model="tier-model")
    user_cfg = _user_cfg("crow_handler", tier)

    monkeypatch.setattr("murder.llm.clients.create_client", lambda provider: _FakeClient())

    client, eff = resolve_role_client_tiered(config, user_cfg, "crow_handler")

    assert isinstance(eff, CrowHandlerConfig)
    assert eff.provider == "anthropic"
    assert eff.model == "tier-model"
    assert eff.poll_interval_s == 12.5


def _minimal_config() -> Config:
    return Config(
        project=ProjectConfig(name="repo"),
        collaborator=HarnessRoleConfig(harness="codex"),
        default_crow=HarnessRoleConfig(harness="codex"),
        crow_handler=CrowHandlerConfig(model="test-model"),
    )


def test_runtime_user_cfg_defaults_none() -> None:
    from murder.app.service.runtime import Runtime

    rt = Runtime(_minimal_config(), Path("/tmp/repo"))
    assert rt.user_cfg is None


def test_runtime_stores_user_cfg() -> None:
    from murder.app.service.runtime import Runtime

    cfg = UserConfig()
    rt = Runtime(_minimal_config(), Path("/tmp/repo"), user_cfg=cfg)
    assert rt.user_cfg is cfg
