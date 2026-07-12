"""Phase 6 — execution policies, batch backends, and Oracle service foundation."""

from __future__ import annotations

from pathlib import Path

import pytest

from murder.llm.backends import (
    BatchInferenceBackend,
    CompletionRequest,
    ImmediateInferenceBackend,
    InferenceStatus,
    SubmittedInference,
    as_immediate_backend,
)
from murder.llm.clients.base import CompletionResult
from murder.llm.execution import resolve_execution_policy
from murder.llm.oracle import OracleService, OracleStore
from murder.llm.policy import DirectLlmResolver, InferenceRequirements
from murder.user_config import (
    BUILTIN_EXECUTION_POLICIES,
    BUILTIN_LLM_POLICIES,
    UserConfig,
    UserExecutionConfig,
    UserExecutionPolicy,
    UserLlmConfig,
    UserLlmMetadata,
    UserLlmModelCatalog,
    UserLlmModelOverride,
    UserLlmProviderSettings,
    UserOracleConfig,
)


def _cfg(*, batch_on_remote: bool = False) -> UserConfig:
    remote_modes = {"immediate", "batch"} if batch_on_remote else {"immediate"}
    return UserConfig(
        llm=UserLlmConfig(
            active_policy="oracle-smart",
            providers={
                "local": UserLlmProviderSettings(
                    type="openai_compatible",
                    enabled=True,
                    metadata=UserLlmMetadata(
                        locality="local",
                        cost_class="free",
                        execution_modes={"immediate"},
                    ),
                    models=UserLlmModelCatalog(source="custom", include=["local-a"]),
                ),
                "remote": UserLlmProviderSettings(
                    type="groq",
                    enabled=True,
                    metadata=UserLlmMetadata(
                        locality="remote",
                        cost_class="free",
                        capabilities={"tools"},
                        execution_modes=remote_modes,
                    ),
                    models=UserLlmModelCatalog(source="custom", include=["remote-a"]),
                ),
            },
            feature_policies={"oracle": "oracle-smart"},
        ),
        execution=UserExecutionConfig(),
        oracle=UserOracleConfig(
            enabled=True,
            model_policy="oracle-smart",
            execution_policy="batch-preferred",
        ),
    )


def test_builtin_execution_policies_and_oracle_smart_exist() -> None:
    assert set(BUILTIN_EXECUTION_POLICIES) == {"immediate", "batch-preferred", "batch-only"}
    assert "oracle-smart" in BUILTIN_LLM_POLICIES
    assert BUILTIN_LLM_POLICIES["oracle-smart"].builtin is True


def test_execution_modes_default_to_immediate_and_filter_requirements() -> None:
    cfg = _cfg()
    assert cfg.llm is not None
    cfg.llm.providers["remote"].models.overrides["remote-a"] = UserLlmModelOverride(
        execution_modes={"batch"}
    )
    resolver = DirectLlmResolver(cfg)
    immediate = resolver.resolve(
        InferenceRequirements(feature_type="oracle", required_execution_mode="immediate")
    )
    # remote-a is batch-only via override; local still matches oracle-smart fallback.
    assert immediate.status == "resolved"
    assert immediate.candidate is not None
    assert immediate.candidate.model_id == "local-a"
    assert "immediate" in immediate.candidate.metadata.execution_modes

    batch = resolver.resolve(
        InferenceRequirements(feature_type="oracle", required_execution_mode="batch")
    )
    assert batch.candidate is not None
    assert batch.candidate.model_id == "remote-a"


def test_batch_preferred_falls_back_when_candidate_is_immediate_only() -> None:
    plan = resolve_execution_policy(
        _cfg(),
        "batch-preferred",
        candidate_modes=frozenset({"immediate"}),
    )
    assert plan.status == "resolved"
    assert plan.modes == ("immediate",)
    assert plan.selected_mode == "immediate"


def test_batch_only_unsupported_without_batch_capability() -> None:
    plan = resolve_execution_policy(
        _cfg(),
        "batch-only",
        candidate_modes=frozenset({"immediate"}),
    )
    assert plan.status == "unsupported"
    assert plan.modes == ()


def test_custom_execution_policy_round_trips() -> None:
    cfg = UserConfig(
        execution=UserExecutionConfig(
            policies={
                "mine": UserExecutionPolicy(
                    name="Mine",
                    preferred_mode="batch",
                    fallback_mode="immediate",
                )
            }
        )
    )
    assert cfg.execution is not None
    assert cfg.execution.resolved_policy("mine") is not None
    assert cfg.execution.resolved_policy("immediate") is not None
    assert cfg.execution.resolved_policy("missing") is None


def test_immediate_backend_adapter_and_batch_protocol() -> None:
    class FakeClient:
        async def complete(self, *, model: str, system: str, messages, **_kwargs):
            return CompletionResult(
                text="ok",
                prompt_tokens=1,
                completion_tokens=1,
                model=model,
                latency_ms=1.0,
            )

    backend = as_immediate_backend(FakeClient())
    assert backend is not None
    assert isinstance(backend, ImmediateInferenceBackend)

    class FakeBatch:
        async def submit(self, request: CompletionRequest) -> SubmittedInference:
            return SubmittedInference(job_id="j1", provider_id="p", model_id=request.model)

        async def status(self, submission: SubmittedInference) -> InferenceStatus:
            return InferenceStatus(status="succeeded")

        async def result(self, submission: SubmittedInference) -> CompletionResult:
            return CompletionResult(
                text="done",
                prompt_tokens=1,
                completion_tokens=1,
                model=submission.model_id,
                latency_ms=1.0,
            )

    batch: BatchInferenceBackend = FakeBatch()
    assert isinstance(batch, BatchInferenceBackend)


@pytest.mark.asyncio
async def test_immediate_adapter_forwards_request() -> None:
    class FakeClient:
        async def complete(self, *, model: str, system: str, messages, **_kwargs):
            return CompletionResult(
                text=messages[0]["content"],
                prompt_tokens=1,
                completion_tokens=1,
                model=model,
                latency_ms=0.5,
            )

    backend = as_immediate_backend(FakeClient())
    assert backend is not None
    result = await backend.complete(
        CompletionRequest(model="m", messages=[{"role": "user", "content": "hi"}])
    )
    assert result.text == "hi"


def test_oracle_prepare_resolve_submit_resume(tmp_path: Path) -> None:
    cfg = _cfg(batch_on_remote=True)
    store = OracleStore(root=tmp_path / "oracle")
    service = OracleService(cfg, store=store)

    prepared = service.prepare("Should we ship?", metadata={"ticket": "t1"})
    assert prepared.phase == "prepared"
    assert prepared.context.question == "Should we ship?"
    assert store.load(prepared.workflow_id) is not None

    resolved = service.resolve(prepared.workflow_id)
    assert resolved.phase == "resolved"
    assert resolved.provider_id == "remote"
    assert resolved.model_id == "remote-a"
    assert resolved.selected_execution_mode == "batch"

    submitted = service.submit(resolved.workflow_id)
    assert submitted.phase == "awaiting"
    assert submitted.submission is not None
    assert submitted.submission["mode"] == "batch"
    assert "job_id" in submitted.submission

    # Resume from awaiting is idempotent and keeps the job handle.
    resumed = service.resume(submitted.workflow_id)
    assert resumed.phase == "awaiting"
    assert resumed.submission is not None
    assert resumed.submission["job_id"] == submitted.submission["job_id"]


def test_oracle_resume_from_prepared_runs_full_pipeline(tmp_path: Path) -> None:
    cfg = _cfg(batch_on_remote=False)
    # Immediate-only candidates: batch-preferred must fall back.
    cfg.oracle = UserOracleConfig(execution_policy="batch-preferred")
    store = OracleStore(root=tmp_path / "oracle")
    service = OracleService(cfg, store=store)

    prepared = service.prepare("resume me")
    done = service.resume(prepared.workflow_id)
    assert done.phase == "completed"
    assert done.selected_execution_mode == "immediate"
    assert done.submission is not None
    assert done.submission["mode"] == "immediate"


def test_oracle_respects_global_llm_disable(tmp_path: Path) -> None:
    cfg = _cfg()
    assert cfg.llm is not None
    cfg.llm.disabled = True
    service = OracleService(cfg, store=OracleStore(root=tmp_path / "oracle"))
    prepared = service.prepare("nope")
    failed = service.resolve(prepared.workflow_id)
    assert failed.phase == "failed"
    assert failed.error is not None
    assert "disabled" in failed.error


def test_oracle_config_defaults_separate_from_model_policy() -> None:
    cfg = UserConfig.model_validate(
        {
            "oracle": {
                "model_policy": "oracle-smart",
                "execution_policy": "batch-preferred",
            },
            "execution": {"policies": {}},
        }
    )
    assert cfg.oracle is not None
    assert cfg.oracle.model_policy == "oracle-smart"
    assert cfg.oracle.execution_policy == "batch-preferred"
    # Model-selection builtins must not encode batch/immediate choice.
    groups = BUILTIN_LLM_POLICIES["oracle-smart"].groups
    dumped = [g.model_dump(mode="json") for g in groups]
    assert "batch" not in str(dumped)
    assert "immediate" not in str(dumped)
