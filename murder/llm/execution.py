"""Execution-policy resolution — how a request is submitted, not which model.

Model-selection policies answer "which provider/model?". Execution policies
answer "immediate vs batch?" (§13.2). They must stay separate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from murder.user_config import UserConfig, UserExecutionPolicy

ExecutionMode = Literal["immediate", "batch"]
ExecutionChoiceStatus = Literal[
    "resolved",
    "disabled",
    "no_policy",
    "unsupported",
]


@dataclass(frozen=True)
class ExecutionPlan:
    """Ordered modes to attempt for one inference submission."""

    status: ExecutionChoiceStatus
    modes: tuple[ExecutionMode, ...] = ()
    policy_name: str | None = None
    # First mode that the candidate advertises, when known.
    selected_mode: ExecutionMode | None = None


def resolve_execution_policy(
    user_cfg: UserConfig | None,
    policy_name: str,
    *,
    candidate_modes: frozenset[str] | None = None,
) -> ExecutionPlan:
    """Resolve an execution policy into an ordered mode attempt list.

    When *candidate_modes* is provided, modes the candidate does not advertise
    are skipped. ``batch-preferred`` therefore falls back to immediate when the
    candidate only supports immediate.
    """
    if user_cfg is not None and user_cfg.llm is not None and user_cfg.llm.disabled:
        return ExecutionPlan(status="disabled", policy_name=policy_name)

    policy = _lookup_policy(user_cfg, policy_name)
    if policy is None:
        return ExecutionPlan(status="no_policy", policy_name=policy_name)

    ordered = _policy_modes(policy)
    if candidate_modes is not None:
        ordered = tuple(mode for mode in ordered if mode in candidate_modes)
    if not ordered:
        return ExecutionPlan(status="unsupported", policy_name=policy_name, modes=())
    return ExecutionPlan(
        status="resolved",
        modes=ordered,
        policy_name=policy_name,
        selected_mode=ordered[0],
    )


def _lookup_policy(user_cfg: UserConfig | None, name: str) -> UserExecutionPolicy | None:
    from murder.user_config import BUILTIN_EXECUTION_POLICIES, UserExecutionConfig

    if name in BUILTIN_EXECUTION_POLICIES:
        return BUILTIN_EXECUTION_POLICIES[name]
    execution = user_cfg.execution if user_cfg is not None else None
    if execution is None:
        execution = UserExecutionConfig()
    return execution.resolved_policy(name)


def _policy_modes(policy: UserExecutionPolicy) -> tuple[ExecutionMode, ...]:
    if policy.mode is not None:
        return (policy.mode,)
    modes: list[ExecutionMode] = []
    if policy.preferred_mode is not None:
        modes.append(policy.preferred_mode)
    if policy.fallback_mode is not None and policy.fallback_mode not in modes:
        modes.append(policy.fallback_mode)
    return tuple(modes)


__all__ = [
    "ExecutionChoiceStatus",
    "ExecutionMode",
    "ExecutionPlan",
    "resolve_execution_policy",
]
