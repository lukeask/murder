"""Selection-only policy resolver for direct LLM inference.

No provider client is constructed here.  The resolver reports the selected
provider/model and an ordered same-group-first fallback sequence, so execution
code can apply its own transport and retry policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from murder.user_config import UserConfig, UserLlmModelOverride

Locality = Literal["local", "remote", "unknown"]
CostClass = Literal["free", "paid", "unknown"]
ResolutionStatus = Literal["resolved", "disabled", "no_policy", "no_candidates"]

_DEFAULT_PROVIDER_METADATA: dict[str, tuple[Locality, CostClass]] = {
    "groq": ("remote", "free"),
    "cerebras": ("remote", "free"),
    "openrouter": ("remote", "unknown"),
    "openai": ("remote", "paid"),
    "anthropic": ("remote", "paid"),
    "local": ("local", "unknown"),
    "lemonade": ("local", "unknown"),
    "openai_compatible": ("local", "unknown"),
}


@dataclass(frozen=True)
class CandidateMetadata:
    locality: Locality = "unknown"
    cost_class: CostClass = "unknown"
    tags: frozenset[str] = frozenset()
    capabilities: frozenset[str] = frozenset()
    execution_modes: frozenset[str] = frozenset()
    context_window: int | None = None


@dataclass(frozen=True)
class ResolvedCandidate:
    provider_id: str
    provider_type: str
    model_id: str
    metadata: CandidateMetadata


@dataclass(frozen=True)
class InferenceRequirements:
    feature_type: str
    required_capabilities: frozenset[str] = frozenset()
    required_execution_mode: str | None = None
    min_context_tokens: int | None = None


@dataclass(frozen=True)
class Resolution:
    status: ResolutionStatus
    candidate: ResolvedCandidate | None = None
    # The candidate is first; later values are same-group alternatives before
    # lower-priority groups.  They are intentionally client-free.
    candidates: tuple[ResolvedCandidate, ...] = ()
    policy_name: str | None = None

    @classmethod
    def disabled_globally(cls) -> Resolution:
        return cls(status="disabled")


@dataclass
class DirectLlmResolver:
    """Stateful resolver with one round-robin cursor per policy group."""

    user_cfg: UserConfig | None
    # Tests and future discovery services can supply source model metadata by
    # provider type.  Production discovery is deliberately outside this layer.
    recommended_catalogs: dict[str, dict[str, CandidateMetadata]] = field(default_factory=dict)
    discovered_catalogs: dict[str, dict[str, CandidateMetadata]] = field(default_factory=dict)
    _round_robin: dict[tuple[str, int], int] = field(default_factory=dict, init=False)

    def resolve(self, requirements: InferenceRequirements) -> Resolution:
        """Resolve candidates and advance the selected groups' round-robin cursors."""
        return self._resolve(requirements, advance_round_robin=True)

    def preview(self, requirements: InferenceRequirements) -> Resolution:
        """Return the effective candidate order without changing round-robin state.

        Settings and diagnostics use this to display a feature's selected
        policy.  A preview must not influence which candidate a subsequent
        inference request receives.
        """
        return self._resolve(requirements, advance_round_robin=False)

    def _resolve(
        self, requirements: InferenceRequirements, *, advance_round_robin: bool
    ) -> Resolution:
        if self.user_cfg is None or self.user_cfg.llm is None or self.user_cfg.llm.disabled:
            return Resolution.disabled_globally()
        llm = self.user_cfg.llm
        policy_name = llm.feature_policies.get(requirements.feature_type, llm.active_policy)
        if policy_name in (None, "disabled"):
            return Resolution(status="no_policy", policy_name=policy_name)
        policy = llm.resolved_policy(policy_name)
        if policy is None:
            return Resolution(status="no_policy", policy_name=policy_name)

        seen: set[tuple[str, str]] = set()
        ordered: list[ResolvedCandidate] = []
        for index, group in enumerate(policy.groups):
            group_candidates = self._expand_group(group.selectors, requirements, seen)
            if not group_candidates:
                continue
            key = (policy_name, index)
            cursor = self._round_robin.get(key, 0) % len(group_candidates)
            if advance_round_robin:
                self._round_robin[key] = cursor + 1
            rotated = group_candidates[cursor:] + group_candidates[:cursor]
            ordered.extend(rotated)
        if not ordered:
            return Resolution(status="no_candidates", policy_name=policy_name)
        return Resolution(
            status="resolved",
            candidate=ordered[0],
            candidates=tuple(ordered),
            policy_name=policy_name,
        )

    def _expand_group(
        self, selectors: object, requirements: InferenceRequirements, seen: set[tuple[str, str]]
    ) -> list[ResolvedCandidate]:
        assert self.user_cfg is not None and self.user_cfg.llm is not None
        out: list[ResolvedCandidate] = []
        for selector in selectors:  # UserLlmSelector; kept structural to avoid runtime cycle
            for candidate in self._expand_selector(selector):
                identity = (candidate.provider_id, candidate.model_id)
                if identity in seen or not _supports(candidate.metadata, requirements):
                    continue
                seen.add(identity)
                out.append(candidate)
        return out

    def _expand_selector(self, selector: object) -> list[ResolvedCandidate]:
        assert self.user_cfg is not None and self.user_cfg.llm is not None
        llm = self.user_cfg.llm
        # Exact candidates and metadata matches are mutually useful in the same
        # group; malformed old config simply produces no candidates.
        exact = getattr(selector, "candidate", None)
        match = getattr(selector, "match", None)
        if exact is not None:
            provider = llm.providers.get(exact.provider)
            if provider is None:
                return []
            return self._provider_candidates(exact.provider, provider, only_model=exact.model)
        if match is None:
            return []
        out: list[ResolvedCandidate] = []
        for provider_id, provider in llm.providers.items():
            for candidate in self._provider_candidates(provider_id, provider):
                metadata = candidate.metadata
                if match.locality is not None and metadata.locality != match.locality:
                    continue
                if match.cost_class is not None and metadata.cost_class != match.cost_class:
                    continue
                if not set(match.tags).issubset(metadata.tags):
                    continue
                if not set(match.capabilities).issubset(metadata.capabilities):
                    continue
                out.append(candidate)
        return out

    def _provider_candidates(
        self,
        provider_id: str,
        provider: object,
        only_model: str | None = None,
    ) -> list[ResolvedCandidate]:
        if not provider.enabled:
            return []
        provider_type = provider.type or provider_id
        catalog = provider.models
        source = (
            self.recommended_catalogs
            if catalog.source == "recommended"
            else self.discovered_catalogs
        )
        base = dict(source.get(provider_type, {})) if catalog.source != "custom" else {}
        model_ids = list(base)
        model_ids.extend(model for model in catalog.include if model not in base)
        model_ids = [model for model in model_ids if model not in set(catalog.exclude)]
        if only_model is not None:
            # Exact pins are allowed for advanced custom models even when the
            # selected source has not discovered them yet.
            model_ids = [only_model] if only_model not in catalog.exclude else []
        out: list[ResolvedCandidate] = []
        for model_id in model_ids:
            override = catalog.overrides.get(model_id)
            if override is not None and override.enabled is False:
                continue
            metadata = _effective_metadata(
                base.get(model_id), provider.metadata, override, provider_type
            )
            out.append(ResolvedCandidate(provider_id, provider_type, model_id, metadata))
        return out


def _effective_metadata(
    base: CandidateMetadata | None,
    provider: object,
    override: UserLlmModelOverride | None,
    provider_type: str,
) -> CandidateMetadata:
    def value(name: str, default: object) -> object:
        override_value = getattr(override, name, None) if override is not None else None
        if override_value is not None:
            return override_value
        provider_value = getattr(provider, name, None)
        if provider_value is not None:
            return provider_value
        base_value = getattr(base, name) if base is not None else None
        if base_value is not None:
            return base_value
        if name == "locality":
            return _DEFAULT_PROVIDER_METADATA.get(provider_type, ("unknown", "unknown"))[0]
        if name == "cost_class":
            return _DEFAULT_PROVIDER_METADATA.get(provider_type, ("unknown", "unknown"))[1]
        return default

    # Unspecified modes mean immediate-only: every current adapter is
    # request/response, and batch support must be advertised explicitly.
    modes = frozenset(value("execution_modes", frozenset({"immediate"})))
    if not modes:
        modes = frozenset({"immediate"})
    return CandidateMetadata(
        locality=value("locality", "unknown"),  # type: ignore[arg-type]
        cost_class=value("cost_class", "unknown"),  # type: ignore[arg-type]
        tags=frozenset(value("tags", frozenset())),
        capabilities=frozenset(value("capabilities", frozenset())),
        execution_modes=modes,
        context_window=value("context_window", None),  # type: ignore[arg-type]
    )


def _supports(metadata: CandidateMetadata, requirements: InferenceRequirements) -> bool:
    if not requirements.required_capabilities.issubset(metadata.capabilities):
        return False
    if (
        requirements.required_execution_mode is not None
        and requirements.required_execution_mode not in metadata.execution_modes
    ):
        return False
    return requirements.min_context_tokens is None or (
        metadata.context_window is not None
        and metadata.context_window >= requirements.min_context_tokens
    )


__all__ = [
    "CandidateMetadata",
    "DirectLlmResolver",
    "InferenceRequirements",
    "Resolution",
    "ResolvedCandidate",
    "direct_llm_is_enabled",
]


def direct_llm_is_enabled(user_cfg: UserConfig | None) -> bool:
    """Compatibility helper for direct callers that only need the global gate."""
    return user_cfg is not None and user_cfg.llm is not None and not user_cfg.llm.disabled
