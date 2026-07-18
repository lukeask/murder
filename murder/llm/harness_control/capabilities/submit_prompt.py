"""Verified prompt-submission reconciler.

The reconciler is deliberately pure.  It never emits a key, sleeps, or trusts
an emission result as an acknowledgment; runtime code persists and executes
the selected semantic action separately.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime
from uuid import uuid4

from murder.llm.harness_control.model.actions import (
    PASTE_VERIFICATION_TAIL_LENGTH,
    ClearComposer,
    CommitPromptSubmission,
    DuplicatePolicy,
    InputProvenance,
    InsertPromptPayload,
)
from murder.llm.harness_control.model.observations import (
    ComposerActionability,
    Knowledge,
    ObservationSnapshot,
)
from murder.llm.harness_control.model.operations import (
    ControllerDecision,
    ControllerDecisionKind,
    OperationStatus,
    SubmitPhase,
    SubmitPromptOperation,
)
from murder.llm.harness_control.model.predicates import PredicateResult, TruthValue


def _predicate(
    snapshot: ObservationSnapshot, name: str, value: TruthValue, reason: str
) -> PredicateResult:
    return PredicateResult(value, name, (), snapshot.revision, reason)


def _visual_rows_match_payload(observed: str, expected: str) -> bool:
    """Match composer rows without inventing whether their boundaries are whitespace."""

    positions = {0}
    for index, row in enumerate(observed.splitlines()):
        content = re.sub(r"\s+", " ", row).strip()
        separators = ("",) if index == 0 else ("", " ")
        positions = {
            position + len(separator) + len(content)
            for position in positions
            for separator in separators
            if expected.startswith(separator + content, position)
        }
        if not positions:
            return False
    return len(expected) in positions


def composer_contains_payload(
    op: SubmitPromptOperation, snapshot: ObservationSnapshot
) -> PredicateResult:
    composer = snapshot.composer
    if composer.knowledge is not Knowledge.PRESENT or composer.value is None:
        return _predicate(
            snapshot,
            "composer_contains_payload",
            TruthValue.UNKNOWN,
            f"composer is {composer.knowledge.name.lower()}",
        )
    fingerprint = composer.value.content_fingerprint
    if fingerprint is None:
        return _predicate(
            snapshot,
            "composer_contains_payload",
            TruthValue.UNKNOWN,
            "composer fingerprint is unavailable",
        )
    collapsed = re.fullmatch(
        r"\[Pasted Content (?P<count>\d+) chars\]\s*(?P<tail>.+)",
        composer.value.normalized_text or "",
    )
    if (
        collapsed is not None
        and len(op.request.payload.chunks) == 1
        and op.request.payload.chunks[0].provenance is not InputProvenance.USER_TYPED
    ):
        expected = op.request.payload.normalized_text
        tail = collapsed.group("tail")
        prefix_length = len(expected) - len(tail)
        verified = (
            len(tail) == PASTE_VERIFICATION_TAIL_LENGTH
            and expected.endswith(tail)
            and int(collapsed.group("count")) == prefix_length + 1
        )
        return _predicate(
            snapshot,
            "composer_contains_payload",
            TruthValue.TRUE if verified else TruthValue.FALSE,
            "collapsed paste count and payload-derived visible tail compared with intended payload",
        )
    verified = fingerprint == op.payload_fingerprint
    composer_text = composer.value.text
    if not verified and composer_text is not None and "\n" in composer_text:
        verified = _visual_rows_match_payload(
            composer_text,
            op.request.payload.normalized_text,
        )
    return _predicate(
        snapshot,
        "composer_contains_payload",
        TruthValue.TRUE if verified else TruthValue.FALSE,
        "composer visual rows compared with intended payload",
    )


def prompt_entry_surface_ready(snapshot: ObservationSnapshot) -> PredicateResult:
    surface, composer = snapshot.surface, snapshot.composer
    if surface.knowledge is not Knowledge.PRESENT or composer.knowledge is not Knowledge.PRESENT:
        return _predicate(
            snapshot,
            "prompt_entry_surface_ready",
            TruthValue.UNKNOWN,
            "surface or composer is not known",
        )
    assert surface.value is not None and composer.value is not None
    ready = (
        not surface.value.blocks_composer_input
        and composer.value.actionability is ComposerActionability.ACTIONABLE
        and composer.value.accepts_submission is not False
    )
    return _predicate(
        snapshot,
        "prompt_entry_surface_ready",
        TruthValue.TRUE if ready else TruthValue.FALSE,
        "composer actionability and surface blocking state evaluated",
    )


def submission_acknowledged(
    op: SubmitPromptOperation, snapshot: ObservationSnapshot
) -> PredicateResult:
    if (
        op.commit_action_id is None
        or op.baseline_revision is None
        or snapshot.revision <= op.baseline_revision
    ):
        return _predicate(
            snapshot,
            "submission_acknowledged",
            TruthValue.UNKNOWN,
            "no fresh post-commit observation",
        )
    tail = snapshot.transcript_tail
    composer = snapshot.composer
    if tail.knowledge is Knowledge.PRESENT and tail.value is not None:
        if op.payload_fingerprint in tail.value.visible_user_fingerprints:
            return _predicate(
                snapshot,
                "submission_acknowledged",
                TruthValue.TRUE,
                "new matching user turn is visible",
            )
        if (
            tail.value.transcript_revision > (op.baseline_transcript_revision or -1)
            and tail.value.assistant_streaming
        ):
            return _predicate(
                snapshot,
                "submission_acknowledged",
                TruthValue.TRUE,
                "transcript advanced and generation started",
            )
    if composer.knowledge is Knowledge.PRESENT and composer.value is not None:
        if composer.value.content_fingerprint == op.payload_fingerprint:
            return _predicate(
                snapshot,
                "submission_acknowledged",
                TruthValue.FALSE,
                "composer still holds exact submitted payload",
            )
        if composer.value.text == "" or composer.value.normalized_text == "":
            return _predicate(
                snapshot,
                "submission_acknowledged",
                TruthValue.TRUE,
                "composer is empty after commit",
            )
    return _predicate(
        snapshot,
        "submission_acknowledged",
        TruthValue.UNKNOWN,
        "no sufficient post-commit acknowledgment signal",
    )


def payload_observation_is_fresh(
    op: SubmitPromptOperation, snapshot: ObservationSnapshot
) -> PredicateResult:
    """Require a capture newer than the recorded payload-insertion boundary."""

    if op.insertion_revision is None:
        return _predicate(
            snapshot,
            "payload_observation_is_fresh",
            TruthValue.UNKNOWN,
            "no recorded insertion observation boundary",
        )
    return _predicate(
        snapshot,
        "payload_observation_is_fresh",
        TruthValue.TRUE if snapshot.revision > op.insertion_revision else TruthValue.FALSE,
        "capture revision compared with insertion boundary",
    )


def assistant_completion_acknowledged(
    op: SubmitPromptOperation, snapshot: ObservationSnapshot
) -> PredicateResult:
    """Require a post-commit completed assistant turn, not only a new frame."""

    tail = snapshot.transcript_tail
    if tail.knowledge is not Knowledge.PRESENT or tail.value is None:
        return _predicate(
            snapshot,
            "assistant_completion_acknowledged",
            TruthValue.UNKNOWN,
            "transcript tail is not available",
        )
    if op.baseline_transcript_revision is not None and (
        tail.value.transcript_revision <= op.baseline_transcript_revision
    ):
        return _predicate(
            snapshot,
            "assistant_completion_acknowledged",
            TruthValue.UNKNOWN,
            "assistant transcript has not advanced beyond the commit boundary",
        )
    if tail.value.assistant_completed and tail.value.last_assistant_turn is not None:
        return _predicate(
            snapshot,
            "assistant_completion_acknowledged",
            TruthValue.TRUE,
            "post-commit assistant turn is complete",
        )
    if tail.value.assistant_streaming:
        return _predicate(
            snapshot,
            "assistant_completion_acknowledged",
            TruthValue.UNKNOWN,
            "assistant turn is still streaming",
        )
    return _predicate(
        snapshot,
        "assistant_completion_acknowledged",
        TruthValue.UNKNOWN,
        "no completed post-commit assistant turn is visible",
    )


def reconcile_submit_prompt(  # noqa: PLR0911, PLR0912 -- typed state machine branches by phase
    op: SubmitPromptOperation, snapshot: ObservationSnapshot, now: datetime
) -> ControllerDecision:
    completion_deadline = (
        op.commit_emitted_at + (op.request.completion_deadline or op.request.submission_deadline)
        if op.commit_emitted_at is not None
        else None
    )
    deadline = (
        completion_deadline
        if op.envelope.phase is SubmitPhase.AWAITING_COMPLETION and completion_deadline is not None
        else op.envelope.deadline
    )
    if deadline is not None and now > deadline:
        if op.envelope.phase in {SubmitPhase.COMMIT_EMITTED, SubmitPhase.AWAITING_ACKNOWLEDGMENT}:
            return ControllerDecision(
                ControllerDecisionKind.ESCALATE,
                SubmitPhase.AMBIGUOUS,
                None,
                "commit emitted without verified acknowledgment",
            )
        return ControllerDecision(
            ControllerDecisionKind.FAIL,
            SubmitPhase.FAILED,
            None,
            "prompt submission deadline exceeded",
        )
    if snapshot.health.requires_escalation:
        return ControllerDecision(
            ControllerDecisionKind.ESCALATE,
            SubmitPhase.ESCALATED,
            None,
            "observation health requires escalation",
        )
    phase = op.envelope.phase
    if phase is SubmitPhase.CREATED:
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            SubmitPhase.ESTABLISHING_SAFE_SURFACE,
            None,
            "begin prompt submission",
        )
    if phase is SubmitPhase.ESTABLISHING_SAFE_SURFACE:
        predicate = prompt_entry_surface_ready(snapshot)
        if predicate.value is TruthValue.TRUE:
            return ControllerDecision(
                ControllerDecisionKind.OBSERVE_MORE,
                SubmitPhase.CLEARING_COMPOSER,
                None,
                "composer is actionable",
                (predicate,),
            )
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            phase,
            None,
            "wait for an actionable composer",
            (predicate,),
        )
    if phase is SubmitPhase.CLEARING_COMPOSER:
        match = composer_contains_payload(op, snapshot)
        composer = snapshot.composer
        if op.clearing_revision is not None and snapshot.revision <= op.clearing_revision:
            return ControllerDecision(
                ControllerDecisionKind.OBSERVE_MORE,
                phase,
                None,
                "wait for a composer observation newer than the clear action",
                (match,),
            )
        if composer.knowledge is not Knowledge.PRESENT or composer.value is None:
            return ControllerDecision(
                ControllerDecisionKind.OBSERVE_MORE,
                phase,
                None,
                "composer contents are unknown",
                (match,),
            )
        if composer.value.content_fingerprint is None:
            return ControllerDecision(
                ControllerDecisionKind.OBSERVE_MORE,
                phase,
                None,
                "composer contents are unverifiable",
                (match,),
            )
        # Fingerprints are hashes (including the hash of an empty string), so
        # emptiness must be read from the observed composer text rather than
        # comparing a hash to ``""``.  The old comparison repeatedly cleared a
        # genuinely empty composer and never advanced to insertion.
        if composer.value.text == "" or composer.value.normalized_text == "":
            return ControllerDecision(
                ControllerDecisionKind.OBSERVE_MORE,
                SubmitPhase.INSERTING_PAYLOAD,
                None,
                "composer is empty",
                (match,),
            )
        action = ClearComposer(
            str(uuid4()),
            op.envelope.operation_id,
            DuplicatePolicy.REPLAY_SAFE_WHILE_PRECONDITION_HOLDS,
        )
        return ControllerDecision(
            ControllerDecisionKind.EMIT_ACTION,
            SubmitPhase.CLEARING_COMPOSER,
            action,
            "clear conflicting composer contents",
            (match,),
        )
    if phase is SubmitPhase.INSERTING_PAYLOAD:
        if op.clearing_revision is not None:
            composer = snapshot.composer
            empty = (
                composer.knowledge is Knowledge.PRESENT
                and composer.value is not None
                and snapshot.revision > op.clearing_revision
                and (composer.value.text == "" or composer.value.normalized_text == "")
            )
            if not empty:
                return ControllerDecision(
                    ControllerDecisionKind.OBSERVE_MORE,
                    SubmitPhase.CLEARING_COMPOSER,
                    None,
                    "revalidate an empty composer after the clear action before insertion",
                )
        action = InsertPromptPayload(
            str(uuid4()),
            op.envelope.operation_id,
            DuplicatePolicy.SAFE_BEFORE_COMMIT,
            tuple(op.request.payload.chunks),
            op.payload_fingerprint,
        )
        return ControllerDecision(
            ControllerDecisionKind.EMIT_ACTION,
            SubmitPhase.VERIFYING_PAYLOAD,
            action,
            "insert intended prompt",
        )
    if phase is SubmitPhase.VERIFYING_PAYLOAD:
        freshness = payload_observation_is_fresh(op, snapshot)
        predicate = composer_contains_payload(op, snapshot)
        if freshness.value is TruthValue.TRUE and predicate.value is TruthValue.TRUE:
            return ControllerDecision(
                ControllerDecisionKind.OBSERVE_MORE,
                SubmitPhase.READY_TO_COMMIT,
                None,
                "payload identity verified",
                (freshness, predicate),
            )
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            phase,
            None,
            "wait for verifiable payload identity",
            (freshness, predicate),
        )
    if phase is SubmitPhase.READY_TO_COMMIT:
        if op.commit_action_id is not None:
            return ControllerDecision(
                ControllerDecisionKind.ESCALATE,
                SubmitPhase.AMBIGUOUS,
                None,
                "a commit action already exists",
            )
        action = CommitPromptSubmission(
            str(uuid4()), op.envelope.operation_id, DuplicatePolicy.AMBIGUOUS_AFTER_EMISSION
        )
        return ControllerDecision(
            ControllerDecisionKind.EMIT_ACTION,
            SubmitPhase.COMMIT_EMITTED,
            action,
            "commit verified prompt",
        )
    if phase in {SubmitPhase.COMMIT_EMITTED, SubmitPhase.AWAITING_ACKNOWLEDGMENT}:
        predicate = submission_acknowledged(op, snapshot)
        if predicate.value is TruthValue.TRUE:
            if op.request.await_completion:
                return ControllerDecision(
                    ControllerDecisionKind.OBSERVE_MORE,
                    SubmitPhase.SUBMISSION_CONFIRMED,
                    None,
                    "prompt submission acknowledged; await assistant completion",
                    (predicate,),
                )
            return ControllerDecision(
                ControllerDecisionKind.SUCCEED,
                SubmitPhase.SUCCEEDED,
                None,
                "prompt submission acknowledged",
                (predicate,),
            )
        if predicate.value is TruthValue.FALSE:
            return ControllerDecision(
                ControllerDecisionKind.ESCALATE,
                SubmitPhase.AMBIGUOUS,
                None,
                "post-commit evidence contradicts submission",
                (predicate,),
            )
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            SubmitPhase.AWAITING_ACKNOWLEDGMENT,
            None,
            "await fresh submission acknowledgment",
            (predicate,),
        )
    if phase is SubmitPhase.SUBMISSION_CONFIRMED:
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            SubmitPhase.AWAITING_COMPLETION,
            None,
            "submission is acknowledged; wait for correlated assistant completion",
        )
    if phase is SubmitPhase.AWAITING_COMPLETION:
        predicate = assistant_completion_acknowledged(op, snapshot)
        if predicate.value is TruthValue.TRUE:
            return ControllerDecision(
                ControllerDecisionKind.SUCCEED,
                SubmitPhase.SUCCEEDED,
                None,
                "post-commit assistant completion acknowledged",
                (predicate,),
            )
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            phase,
            None,
            "await correlated assistant completion",
            (predicate,),
        )
    if phase is SubmitPhase.AMBIGUOUS:
        return ControllerDecision(
            ControllerDecisionKind.ESCALATE,
            SubmitPhase.ESCALATED,
            None,
            op.ambiguity_reason or "ambiguous prompt commit",
        )
    return ControllerDecision(
        ControllerDecisionKind.FAIL, SubmitPhase.FAILED, None, f"invalid submit phase {phase.name}"
    )


def advance_submit_prompt(
    op: SubmitPromptOperation,
    decision: ControllerDecision,
    snapshot: ObservationSnapshot,
    now: datetime,
) -> SubmitPromptOperation:
    """Apply a pure decision as durable operation state before effect emission."""

    status = op.envelope.status
    if decision.kind is ControllerDecisionKind.SUCCEED:
        status = OperationStatus.SUCCEEDED
    elif decision.kind is ControllerDecisionKind.FAIL:
        status = OperationStatus.FAILED
    elif decision.kind is ControllerDecisionKind.ESCALATE:
        status = OperationStatus.ESCALATED
    elif status is OperationStatus.PENDING:
        status = OperationStatus.RUNNING
    action_history = op.envelope.action_history
    insertion_action_id = op.insertion_action_id
    insertion_revision = op.insertion_revision
    clearing_revision = op.clearing_revision
    commit_action_id = op.commit_action_id
    baseline_revision = op.baseline_revision
    baseline_transcript_revision = op.baseline_transcript_revision
    commit_emitted_at = op.commit_emitted_at
    ambiguity_reason = op.ambiguity_reason
    acknowledged_turn = op.acknowledged_turn
    completion_turn = op.completion_turn
    action = decision.action
    if action is not None:
        action_history = (*action_history, action.action_id)
        if isinstance(action, ClearComposer):
            clearing_revision = snapshot.revision
        elif isinstance(action, InsertPromptPayload):
            insertion_action_id = action.action_id
            insertion_revision = snapshot.revision
        elif isinstance(action, CommitPromptSubmission):
            commit_action_id = action.action_id
            baseline_revision = snapshot.revision
            tail = snapshot.transcript_tail.value
            baseline_transcript_revision = tail.transcript_revision if tail is not None else None
            commit_emitted_at = now
    if decision.kind is ControllerDecisionKind.ESCALATE:
        ambiguity_reason = decision.reason
    tail = snapshot.transcript_tail.value
    if decision.next_phase in {SubmitPhase.SUBMISSION_CONFIRMED, SubmitPhase.AWAITING_COMPLETION}:
        acknowledged_turn = tail.last_user_turn if tail is not None else acknowledged_turn
    if decision.kind is ControllerDecisionKind.SUCCEED and op.request.await_completion:
        completion_turn = tail.last_assistant_turn if tail is not None else completion_turn
    return replace(
        op,
        envelope=replace(
            op.envelope,
            phase=decision.next_phase
            if isinstance(decision.next_phase, SubmitPhase)
            else op.envelope.phase,
            status=status,
            updated_at=now,
            last_observation_revision=snapshot.revision,
            action_history=action_history,
        ),
        insertion_action_id=insertion_action_id,
        insertion_revision=insertion_revision,
        clearing_revision=clearing_revision,
        commit_action_id=commit_action_id,
        baseline_revision=baseline_revision,
        baseline_transcript_revision=baseline_transcript_revision,
        commit_emitted_at=commit_emitted_at,
        acknowledged_turn=acknowledged_turn,
        completion_turn=completion_turn,
        ambiguity_reason=ambiguity_reason,
    )
