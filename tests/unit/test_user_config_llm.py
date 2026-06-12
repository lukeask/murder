"""User-scope LLM config: schema round-trip, tier resolution, env application,
file perms, and native_coding_crow gate-out behavior (scrub on user load, raise
on project Config.load).
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from murder.config import Config
from murder.user_config import (
    BUILTIN_TIERS,
    UserConfig,
    UserLlmConfig,
    UserLlmProviderSettings,
    UserLlmTier,
    apply_llm_env,
    config_path,
    load_user_config,
    resolve_tier,
    save_user_config,
)


# --- schema round-trip / stale load ---


def test_llm_schema_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    cfg = UserConfig(
        llm=UserLlmConfig(
            providers={"groq": UserLlmProviderSettings(api_key="k", base_url="b")},
            tiers={"fast": UserLlmTier(provider="groq", model="m", auto_free=True)},
            roles={"crow": "fast"},
        )
    )
    save_user_config(cfg)
    loaded = load_user_config()
    assert loaded.llm is not None
    assert loaded.llm.providers["groq"].api_key == "k"
    assert loaded.llm.providers["groq"].base_url == "b"
    assert loaded.llm.tiers["fast"].model == "m"
    assert loaded.llm.tiers["fast"].auto_free is True
    assert loaded.llm.roles == {"crow": "fast"}


def test_stale_config_without_llm_loads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("tui:\n  modifier: ctrl\n", encoding="utf-8")
    cfg = load_user_config()
    assert cfg.llm is None
    assert cfg.tui.modifier == "ctrl"


# --- resolve_tier ---


def test_resolve_tier_builtin() -> None:
    cfg = UserConfig(llm=UserLlmConfig(roles={"crow": "cheap"}))
    tier = resolve_tier(cfg, "crow")
    assert tier == BUILTIN_TIERS["cheap"]


def test_resolve_tier_user_override_wins() -> None:
    cfg = UserConfig(
        llm=UserLlmConfig(
            tiers={"cheap": UserLlmTier(provider="openai", model="custom")},
            roles={"crow": "cheap"},
        )
    )
    tier = resolve_tier(cfg, "crow")
    assert tier is not None
    assert tier.provider == "openai"
    assert tier.model == "custom"


def test_resolve_tier_unknown_tier_name() -> None:
    cfg = UserConfig(llm=UserLlmConfig(roles={"crow": "nope"}))
    assert resolve_tier(cfg, "crow") is None


def test_resolve_tier_no_role_mapping() -> None:
    cfg = UserConfig(llm=UserLlmConfig())
    assert resolve_tier(cfg, "crow") is None


def test_resolve_tier_no_llm() -> None:
    assert resolve_tier(UserConfig(), "crow") is None
    assert resolve_tier(None, "crow") is None


# --- apply_llm_env ---


def test_apply_llm_env_setdefault_does_not_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "from-env")
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    monkeypatch.delenv("LOCAL_OPENAI_BASE_URL", raising=False)
    cfg = UserConfig(
        llm=UserLlmConfig(
            providers={
                "groq": UserLlmProviderSettings(api_key="from-config"),
                "cerebras": UserLlmProviderSettings(api_key="cb"),
                "local": UserLlmProviderSettings(base_url="http://local"),
            }
        )
    )
    apply_llm_env(cfg)
    import os

    # Existing env var wins.
    assert os.environ["GROQ_API_KEY"] == "from-env"
    # Missing ones are filled from config.
    assert os.environ["CEREBRAS_API_KEY"] == "cb"
    assert os.environ["LOCAL_OPENAI_BASE_URL"] == "http://local"


def test_apply_llm_env_skips_empty_and_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    cfg = UserConfig(
        llm=UserLlmConfig(providers={"openrouter": UserLlmProviderSettings(api_key="")})
    )
    apply_llm_env(cfg)
    import os

    assert "OPENROUTER_API_KEY" not in os.environ


def test_apply_llm_env_no_llm_is_noop() -> None:
    apply_llm_env(UserConfig())
    apply_llm_env(None)


# --- save perms ---


def test_save_user_config_is_owner_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    save_user_config(UserConfig())
    mode = stat.S_IMODE(config_path().stat().st_mode)
    assert mode == 0o600


# --- native_coding_crow gate-out ---


def test_user_config_scrubs_native_coding_crow_scalar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "collaborator:\n  harness: native_coding_crow\n", encoding="utf-8"
    )
    # Must not raise; the gated harness scalar is dropped.
    cfg = load_user_config()
    assert cfg.collaborator is None or cfg.collaborator.harness is None


def test_user_config_scrubs_native_coding_crow_in_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "default_crow:\n"
        "  harness: cursor\n"
        "  harnesses:\n"
        "    - cursor\n"
        "    - native_coding_crow\n",
        encoding="utf-8",
    )
    cfg = load_user_config()
    assert cfg.default_crow is not None
    assert cfg.default_crow.harnesses == ["cursor"]


def test_config_load_raises_on_native_coding_crow_in_roles(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".murder").mkdir(parents=True)
    (repo / ".murder" / "roles.yaml").write_text(
        "default_crow:\n  harness: native_coding_crow\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="native_coding_crow is not available in v0"):
        Config.load(repo)
