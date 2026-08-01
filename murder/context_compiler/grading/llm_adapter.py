"""LLM adapter implementing ``ContextGrader`` via ``APIClient``.

Domain records depend on none of this module. Model choice goes through
``resolve_policy_client`` / ``DirectLlmResolver``; no vendor model is named here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from murder.context_compiler.grading.errors import GraderOutputError
from murder.context_compiler.grading.models import GradeResult
from murder.context_compiler.grading.policy import (
    GRADING_FEATURE_TYPE,
    GRADING_REQUIRED_CAPABILITY,
    MAX_GRADING_OUTPUT_TOKENS,
    MAX_STRUCTURED_OUTPUT_RETRIES,
)
from murder.context_compiler.grading.preview import render_proposal_preview
from murder.context_compiler.grading.rubrics import rubric_for_profile
from murder.context_compiler.grading.structured import (
    SUBMIT_GRADES_TOOL_NAME,
    ValidationError,
    extract_rationales,
    grade_result_json_schema,
    parse_grade_result,
    parse_grade_result_json,
)
from murder.context_compiler.grading.trace import GradingTrace
from murder.context_compiler.models import ContextRequest
from murder.context_compiler.ranking.models import CorpusProposal
from murder.llm.clients.base import APIClient, ToolSpec
from murder.llm.direct import resolve_policy_client
from murder.llm.policy import InferenceRequirements

try:
    from murder.user_config import load_user_config
except ImportError:  # pragma: no cover - defensive
    load_user_config = None  # type: ignore[assignment]


@dataclass
class LlmContextGrader:
    """``ContextGrader`` backed by a bound ``APIClient`` and model id.

    Structured output is requested via a single ``submit_grades`` tool. On
    validation failure the call is retried once with the error text
    (``MAX_STRUCTURED_OUTPUT_RETRIES``). A second failure raises
    ``GraderOutputError`` so ``CorpusGrader`` can fall back without stacking
    another retry.
    """

    client: APIClient
    model: str
    worktree_root: Path
    conn: Any = None  # sqlite3.Connection | None — typed loosely to avoid cycle
    max_output_tokens: int = MAX_GRADING_OUTPUT_TOKENS
    temperature: float = 0.0
    last_trace: GradingTrace | None = field(default=None, init=False, repr=False)
    last_rationales: dict[int, str] = field(default_factory=dict, init=False, repr=False)

    async def grade(
        self,
        request: ContextRequest,
        proposal: CorpusProposal,
    ) -> GradeResult:
        trace = GradingTrace()
        self.last_trace = trace
        self.last_rationales = {}
        trace.record("grading_started", "llm", path="", detail=self.model)

        preview = render_proposal_preview(
            request,
            proposal,
            worktree_root=self.worktree_root,
            conn=self.conn,
        )
        system = rubric_for_profile(request.recipient_profile)
        tool = ToolSpec(
            name=SUBMIT_GRADES_TOOL_NAME,
            description=(
                "Submit include/exclude grades for every relevant proposal index "
                "and optional gaps describing missing hints."
            ),
            parameters=grade_result_json_schema(),
        )
        messages: list[dict[str, Any]] = [{"role": "user", "content": preview}]
        last_error: str | None = None

        for _attempt in range(MAX_STRUCTURED_OUTPUT_RETRIES + 1):
            if last_error is not None:
                messages = [
                    {"role": "user", "content": preview},
                    {
                        "role": "user",
                        "content": (
                            "Your previous submit_grades arguments failed validation:\n"
                            f"{last_error}\n"
                            "Call submit_grades again with corrected arguments only."
                        ),
                    },
                ]
                trace.record("grading_repaired", "retry_validation", detail=last_error[:200])

            result = await self.client.complete(
                model=self.model,
                system=system,
                messages=messages,
                tools=[tool],
                max_tokens=self.max_output_tokens,
                temperature=self.temperature,
            )
            payload = _extract_payload(result)
            if payload is None:
                last_error = "no submit_grades tool call and no parseable JSON text"
                continue
            try:
                grade_result = (
                    parse_grade_result(payload)
                    if not isinstance(payload, str)
                    else parse_grade_result_json(payload)
                )
            except (ValidationError, json.JSONDecodeError, TypeError, ValueError) as exc:
                last_error = str(exc)
                continue

            try:
                raw_for_rationale: object = (
                    payload if not isinstance(payload, str) else json.loads(payload)
                )
            except json.JSONDecodeError:
                raw_for_rationale = payload
            self.last_rationales = extract_rationales(raw_for_rationale)
            for idx, rationale in self.last_rationales.items():
                # Rationale is trace-only — never recipient-facing.
                trace.record(
                    "grading_repaired",
                    "rationale",
                    detail=rationale,
                    proposal_index=idx,
                )
            return grade_result

        trace.record("grading_failed", "grader_invalid_output", detail=last_error or "")
        raise GraderOutputError(last_error or "grader_invalid_output")


def build_llm_context_grader(
    *,
    worktree_root: Path | str,
    conn: Any = None,
    client: APIClient | None = None,
    model: str | None = None,
    user_cfg: Any = None,
) -> LlmContextGrader | None:
    """Resolve a grading client through policy infrastructure.

    Returns ``None`` when LLM is disabled or no suitable candidate exists.
    Callers should fall back to Step 4's proposal in that case.
    """
    root = Path(worktree_root)
    if client is not None and model is not None:
        return LlmContextGrader(client=client, model=model, worktree_root=root, conn=conn)

    cfg = user_cfg
    if cfg is None and load_user_config is not None:
        try:
            cfg = load_user_config()
        except Exception:  # pragma: no cover - config read must stay fail-soft
            cfg = None

    requirements = InferenceRequirements(
        feature_type=GRADING_FEATURE_TYPE,
        required_capabilities=frozenset({GRADING_REQUIRED_CAPABILITY}),
    )
    selected = resolve_policy_client(cfg, GRADING_FEATURE_TYPE, requirements=requirements)
    if selected.client is None or selected.model_id is None:
        return None
    return LlmContextGrader(
        client=selected.client,
        model=selected.model_id,
        worktree_root=root,
        conn=conn,
    )


def _extract_payload(result: Any) -> object | None:
    """Prefer ``submit_grades`` tool arguments; else try response text as JSON."""
    tool_calls = getattr(result, "tool_calls", None) or []
    for call in tool_calls:
        name = getattr(call, "name", None)
        if name == SUBMIT_GRADES_TOOL_NAME:
            args = getattr(call, "arguments", None)
            if isinstance(args, dict):
                return args
            if isinstance(args, str):
                return args
    text = getattr(result, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    return None


__all__ = [
    "GraderOutputError",
    "LlmContextGrader",
    "build_llm_context_grader",
]
