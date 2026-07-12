"""Oracle consultation feature service (§13.1).

Oracle is a workflow feature, not a provider. It:

1. prepares an Oracle-specific context;
2. resolves a provider/model through the Oracle model policy;
3. chooses submission mode via the Oracle execution policy;
4. persists or awaits the result;
5. resumes the workflow from persisted state.

Full Oracle workflow UX is out of scope for the parent llm-providers spec;
this module provides the service + persistence/resume foundation.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from murder.llm.backends import CompletionRequest
from murder.llm.execution import ExecutionPlan, resolve_execution_policy
from murder.llm.policy import (
    CandidateMetadata,
    DirectLlmResolver,
    InferenceRequirements,
    Resolution,
)
from murder.user_config import UserConfig, UserOracleConfig, config_dir

OraclePhase = Literal[
    "prepared",
    "resolved",
    "submitted",
    "awaiting",
    "completed",
    "failed",
]
ORACLE_FEATURE_TYPE = "oracle"


class OracleContext(BaseModel):
    """Prepared Oracle consultation payload (prompt + optional attachments)."""

    model_config = ConfigDict(extra="ignore")

    question: str
    system: str = (
        "You are Oracle, a careful consultation assistant. Answer with clear "
        "reasoning and concrete recommendations."
    )
    messages: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_completion_request(self, model: str) -> CompletionRequest:
        messages = list(self.messages)
        if not any(m.get("role") == "user" and m.get("content") == self.question for m in messages):
            messages = [*messages, {"role": "user", "content": self.question}]
        return CompletionRequest(model=model, system=self.system, messages=messages)


class OracleWorkflowState(BaseModel):
    """Persisted Oracle consultation state for resume after restart."""

    model_config = ConfigDict(extra="ignore")

    workflow_id: str
    phase: OraclePhase
    context: OracleContext
    model_policy: str
    execution_policy: str
    provider_id: str | None = None
    model_id: str | None = None
    selected_execution_mode: str | None = None
    submission: dict[str, Any] | None = None
    result_text: str | None = None
    error: str | None = None
    created_at: str
    updated_at: str


@dataclass
class OracleStore:
    """Filesystem-backed Oracle workflow persistence (architecture foundation)."""

    root: Path

    def path_for(self, workflow_id: str) -> Path:
        return self.root / f"{workflow_id}.json"

    def save(self, state: OracleWorkflowState) -> OracleWorkflowState:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.path_for(state.workflow_id)
        tmp = path.with_suffix(".tmp")
        payload = state.model_dump(mode="json")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(path)
        return state

    def load(self, workflow_id: str) -> OracleWorkflowState | None:
        path = self.path_for(workflow_id)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        return OracleWorkflowState.model_validate(raw)

    def list_ids(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(path.stem for path in self.root.glob("*.json"))


def default_oracle_store() -> OracleStore:
    return OracleStore(root=config_dir() / "oracle" / "workflows")


@dataclass
class OracleService:
    """Coordinates Oracle model policy, execution policy, and workflow state."""

    user_cfg: UserConfig | None
    store: OracleStore = field(default_factory=default_oracle_store)
    discovered_catalogs: dict[str, dict[str, CandidateMetadata]] = field(default_factory=dict)

    @property
    def config(self) -> UserOracleConfig:
        if self.user_cfg is not None and self.user_cfg.oracle is not None:
            return self.user_cfg.oracle
        return UserOracleConfig()

    def prepare(self, question: str, *, metadata: dict[str, Any] | None = None) -> OracleWorkflowState:
        """Create and persist a prepared Oracle consultation."""
        cfg = self.config
        if not cfg.enabled:
            raise RuntimeError("Oracle is disabled")
        now = _now()
        state = OracleWorkflowState(
            workflow_id=str(uuid.uuid4()),
            phase="prepared",
            context=OracleContext(question=question, metadata=dict(metadata or {})),
            model_policy=cfg.model_policy,
            execution_policy=cfg.execution_policy,
            created_at=now,
            updated_at=now,
        )
        return self.store.save(state)

    def resolve(self, workflow_id: str) -> OracleWorkflowState:
        """Resolve model + execution mode for a prepared workflow and persist."""
        state = self._require(workflow_id)
        if self.user_cfg is not None and self.user_cfg.llm is not None and self.user_cfg.llm.disabled:
            return self._fail(state, "direct LLM functionality is disabled")
        cfg = self.config
        if not cfg.enabled:
            return self._fail(state, "Oracle is disabled")

        # Bind the feature to the Oracle model policy for this resolution.
        bound = self._with_oracle_feature_policy(cfg.model_policy)
        requirements = InferenceRequirements(feature_type=ORACLE_FEATURE_TYPE)
        resolution = self._resolver(bound).resolve(requirements)
        if resolution.status != "resolved" or resolution.candidate is None:
            return self._fail(state, f"model resolution failed: {resolution.status}")
        candidate = resolution.candidate
        plan = resolve_execution_policy(
            bound,
            cfg.execution_policy,
            candidate_modes=candidate.metadata.execution_modes or frozenset({"immediate"}),
        )
        if plan.status != "resolved" or plan.selected_mode is None:
            return self._fail(state, f"execution policy failed: {plan.status}")

        state.phase = "resolved"
        state.provider_id = candidate.provider_id
        state.model_id = candidate.model_id
        state.selected_execution_mode = plan.selected_mode
        state.error = None
        state.updated_at = _now()
        return self.store.save(state)

    def submit(self, workflow_id: str) -> OracleWorkflowState:
        """Mark a resolved workflow as submitted (batch) or completed (immediate stub).

        Real provider batch transport is intentionally deferred; this records the
        chosen mode and a durable submission handle so resume can continue.
        """
        state = self._require(workflow_id)
        if state.phase not in {"resolved", "submitted", "awaiting"}:
            return self._fail(state, f"cannot submit from phase {state.phase}")
        if not state.provider_id or not state.model_id or not state.selected_execution_mode:
            return self._fail(state, "workflow is missing resolution fields")

        request = state.context.to_completion_request(state.model_id)
        if state.selected_execution_mode == "immediate":
            # Foundation path: persist the request envelope; callers that have a
            # live client can complete outside this service until batch adapters
            # land. Immediate stub completes with an empty result marker.
            state.phase = "completed"
            state.submission = {
                "mode": "immediate",
                "request": request.model_dump(mode="json"),
            }
            state.result_text = state.result_text or ""
            state.error = None
        else:
            job_id = state.submission.get("job_id") if state.submission else None
            job_id = job_id or str(uuid.uuid4())
            state.phase = "awaiting"
            state.submission = {
                "mode": "batch",
                "job_id": job_id,
                "provider_id": state.provider_id,
                "model_id": state.model_id,
                "request": request.model_dump(mode="json"),
            }
            state.error = None
        state.updated_at = _now()
        return self.store.save(state)

    def resume(self, workflow_id: str) -> OracleWorkflowState:
        """Continue a persisted workflow from its last phase.

        ``prepared`` → resolve → submit; ``resolved`` → submit;
        ``submitted``/``awaiting`` re-enters submit (idempotent handle);
        terminal phases are returned unchanged.
        """
        state = self._require(workflow_id)
        if state.phase in {"completed", "failed"}:
            return state
        if state.phase == "prepared":
            state = self.resolve(workflow_id)
            if state.phase == "failed":
                return state
        if state.phase in {"resolved", "submitted", "awaiting"}:
            return self.submit(workflow_id)
        return state

    def load(self, workflow_id: str) -> OracleWorkflowState | None:
        return self.store.load(workflow_id)

    def preview_resolution(self) -> tuple[Resolution, ExecutionPlan]:
        """Settings/diagnostics: model candidates + execution plan without mutating state."""
        cfg = self.config
        bound = self._with_oracle_feature_policy(cfg.model_policy)
        requirements = InferenceRequirements(feature_type=ORACLE_FEATURE_TYPE)
        resolution = self._resolver(bound).preview(requirements)
        modes = (
            resolution.candidate.metadata.execution_modes
            if resolution.candidate is not None
            else None
        )
        plan = resolve_execution_policy(bound, cfg.execution_policy, candidate_modes=modes)
        return resolution, plan

    def _resolver(self, user_cfg: UserConfig | None) -> DirectLlmResolver:
        from murder.llm.clients import catalog

        return DirectLlmResolver(
            user_cfg,
            recommended_catalogs=catalog.recommended_catalogs(),
            discovered_catalogs=self.discovered_catalogs,
        )

    def _with_oracle_feature_policy(self, model_policy: str) -> UserConfig | None:
        if self.user_cfg is None:
            return None
        llm = self.user_cfg.llm
        if llm is None:
            return self.user_cfg
        feature_policies = dict(llm.feature_policies)
        feature_policies[ORACLE_FEATURE_TYPE] = model_policy
        return self.user_cfg.model_copy(
            update={"llm": llm.model_copy(update={"feature_policies": feature_policies})}
        )

    def _require(self, workflow_id: str) -> OracleWorkflowState:
        state = self.store.load(workflow_id)
        if state is None:
            raise KeyError(f"unknown Oracle workflow: {workflow_id}")
        return state

    def _fail(self, state: OracleWorkflowState, error: str) -> OracleWorkflowState:
        state.phase = "failed"
        state.error = error
        state.updated_at = _now()
        return self.store.save(state)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "ORACLE_FEATURE_TYPE",
    "OracleContext",
    "OraclePhase",
    "OracleService",
    "OracleStore",
    "OracleWorkflowState",
    "default_oracle_store",
]
