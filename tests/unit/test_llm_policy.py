from __future__ import annotations

from murder.llm.direct import preview_policy, resolve_direct_role_client
from murder.llm.policy import CandidateMetadata, DirectLlmResolver, InferenceRequirements
from murder.config import ApiRoleConfig
from murder.user_config import (
    UserConfig,
    UserLlmConfig,
    UserLlmExactCandidate,
    UserLlmMetadata,
    UserLlmModelCatalog,
    UserLlmModelOverride,
    UserLlmPolicy,
    UserLlmPolicyGroup,
    UserLlmProviderSettings,
    UserLlmSelector,
    UserLlmSelectorMatch,
)


def _cfg(*, disabled: bool = False) -> UserConfig:
    return UserConfig(
        llm=UserLlmConfig(
            disabled=disabled,
            active_policy="test",
            providers={
                "local": UserLlmProviderSettings(
                    type="openai_compatible", enabled=True,
                    metadata=UserLlmMetadata(locality="local", cost_class="free"),
                    models=UserLlmModelCatalog(source="custom", include=["a", "b"]),
                ),
                "remote": UserLlmProviderSettings(
                    type="groq", enabled=True,
                    metadata=UserLlmMetadata(locality="remote", cost_class="free"),
                    models=UserLlmModelCatalog(source="custom", include=["c"]),
                ),
            },
            policies={
                "test": UserLlmPolicy(groups=[
                    UserLlmPolicyGroup(selectors=[UserLlmSelector(match=UserLlmSelectorMatch(locality="local"))]),
                    UserLlmPolicyGroup(selectors=[UserLlmSelector(candidate=UserLlmExactCandidate(provider="remote", model="c"))]),
                ])
            },
            feature_policies={"summary": "test"},
        )
    )


def test_disabled_short_circuits_before_policy_or_catalog_access() -> None:
    cfg = _cfg(disabled=True)
    result = DirectLlmResolver(cfg).resolve(InferenceRequirements(feature_type="summary"))
    assert result.status == "disabled"
    assert result.candidate is None
    assert cfg.llm is not None and cfg.llm.providers["local"].enabled


def test_groups_round_robin_then_fall_back_in_priority_order() -> None:
    resolver = DirectLlmResolver(_cfg())
    request = InferenceRequirements(feature_type="summary")
    first = resolver.resolve(request)
    second = resolver.resolve(request)
    assert first.status == "resolved"
    assert [candidate.model_id for candidate in first.candidates] == ["a", "b", "c"]
    assert [candidate.model_id for candidate in second.candidates] == ["b", "a", "c"]


def test_preview_does_not_advance_round_robin_selection() -> None:
    resolver = DirectLlmResolver(_cfg())
    request = InferenceRequirements(feature_type="summary")

    preview = resolver.preview(request)
    selected = resolver.resolve(request)

    assert [candidate.model_id for candidate in preview.candidates] == ["a", "b", "c"]
    assert [candidate.model_id for candidate in selected.candidates] == ["a", "b", "c"]


def test_preview_policy_uses_feature_assignment_and_discovered_metadata() -> None:
    cfg = _cfg()
    assert cfg.llm is not None
    provider = cfg.llm.providers["local"]
    provider.models = UserLlmModelCatalog(
        source="discovered",
        include=["included"],
        exclude=["hidden"],
        overrides={"included": UserLlmModelOverride(tags={"smart"})},
    )
    cfg.llm.feature_policies["preview"] = "test"

    result = preview_policy(
        cfg,
        "preview",
        discovered_catalogs={
            "openai_compatible": {
                "hidden": CandidateMetadata(),
                "discovered": CandidateMetadata(capabilities=frozenset({"vision"})),
            }
        },
    )

    assert result.status == "resolved"
    assert [candidate.model_id for candidate in result.candidates] == [
        "discovered",
        "included",
        "c",
    ]
    assert result.candidates[0].metadata.capabilities == frozenset({"vision"})
    assert result.candidates[1].metadata.tags == frozenset({"smart"})


def test_deduplicates_across_groups_and_honors_model_enablement() -> None:
    cfg = _cfg()
    assert cfg.llm is not None
    cfg.llm.policies["test"].groups.append(UserLlmPolicyGroup(selectors=[
        UserLlmSelector(candidate=UserLlmExactCandidate(provider="local", model="a")),
    ]))
    cfg.llm.providers["local"].models.overrides["b"] = UserLlmModelOverride(enabled=False)
    result = DirectLlmResolver(cfg).resolve(InferenceRequirements(feature_type="summary"))
    assert [candidate.model_id for candidate in result.candidates] == ["a", "c"]


def test_metadata_inheritance_and_requirements_filtering() -> None:
    cfg = _cfg()
    assert cfg.llm is not None
    cfg.llm.providers["local"].metadata.capabilities = {"tools"}
    cfg.llm.providers["local"].models.overrides["a"] = UserLlmModelOverride(
        capabilities={"tools", "vision"}, context_window=32
    )
    resolver = DirectLlmResolver(cfg)
    result = resolver.resolve(InferenceRequirements(
        feature_type="summary", required_capabilities=frozenset({"vision"}), min_context_tokens=16
    ))
    assert result.status == "resolved"
    assert result.candidate is not None and result.candidate.model_id == "a"
    assert result.candidate.metadata.locality == "local"


def test_builtin_policy_and_resolution_statuses() -> None:
    cfg = _cfg()
    assert cfg.llm is not None
    cfg.llm.active_policy = "local-then-free"
    assert DirectLlmResolver(cfg).resolve(InferenceRequirements(feature_type="missing")).candidate.model_id == "a"  # type: ignore[union-attr]
    cfg.llm.active_policy = "absent"
    assert DirectLlmResolver(cfg).resolve(InferenceRequirements(feature_type="missing")).status == "no_policy"
    cfg.llm.active_policy = "local-only"
    cfg.llm.providers["local"].enabled = False
    assert DirectLlmResolver(cfg).resolve(InferenceRequirements(feature_type="missing")).status == "no_candidates"


def test_recommended_catalog_include_exclude_and_override() -> None:
    cfg = _cfg()
    assert cfg.llm is not None
    provider = cfg.llm.providers["local"]
    provider.models = UserLlmModelCatalog(
        source="recommended", include=["new"], exclude=["old"],
        overrides={"new": UserLlmModelOverride(cost_class="paid")},
    )
    result = DirectLlmResolver(cfg, recommended_catalogs={
        "openai_compatible": {"old": CandidateMetadata(), "kept": CandidateMetadata(cost_class="free")}
    }).resolve(InferenceRequirements(feature_type="summary"))
    assert [candidate.model_id for candidate in result.candidates] == ["kept", "new", "c"]
    assert result.candidates[1].metadata.cost_class == "paid"


def test_legacy_provider_shape_migrates_without_losing_tier_data() -> None:
    cfg = UserConfig.model_validate({"llm": {
        "direct_llm_enabled": False,
        "providers": {"groq": {"api_key": "secret", "base_url": "https://example.test"}},
        "tiers": {"cheap": {"provider": "groq", "model": "m", "auto_free": True}},
        "roles": {"crow": "cheap"},
    }})
    assert cfg.llm is not None
    assert cfg.llm.disabled is True
    assert cfg.llm.providers["groq"].type == "groq"
    assert cfg.llm.providers["groq"].auth.api_key == "secret"
    assert cfg.llm.providers["groq"].endpoint == "https://example.test"
    assert cfg.llm.roles == {"crow": "cheap"}


def test_runtime_seam_disabled_config_never_constructs_provider(monkeypatch) -> None:
    cfg = _cfg(disabled=True)
    called: list[object] = []
    monkeypatch.setattr(
        "murder.llm.clients.catalog.create_instance_client",
        lambda *args: called.append(args),
    )
    client, effective = resolve_direct_role_client(
        ApiRoleConfig(provider="groq", model="legacy"), cfg, "crow_classification", "crow_handler"
    )
    assert client is None
    assert effective.model == "legacy"
    assert called == []


def test_runtime_seam_uses_feature_policy_model(monkeypatch) -> None:
    cfg = _cfg()
    assert cfg.llm is not None
    cfg.llm.feature_policies["crow_classification"] = "test"
    sentinel = object()
    monkeypatch.setattr(
        "murder.llm.clients.catalog.create_instance_client", lambda *_args: sentinel
    )
    client, effective = resolve_direct_role_client(
        ApiRoleConfig(provider="groq", model="legacy"), cfg, "crow_classification", "crow_handler"
    )
    assert client is sentinel
    assert effective.model in {"a", "b"}
