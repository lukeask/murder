"""Durable execution of explicitly user-directed raw terminal input.

The manual-key endpoint is intentionally *not* a generic escape hatch to
tmux. It creates an operation, semantic action, and lowered effects in the
verified journal before acquiring the already-owned harness actuator. The
receipt reports only terminal transport acceptance; a manual input has no
implied harness-level success condition and is never replayed.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum, auto
from uuid import uuid4

from murder.llm.harness_control.model.actions import (
    DuplicatePolicy,
    EffectEmission,
    EmissionBatchResult,
    EmissionStatus,
    SendLiteralKeys,
    SendNamedKey,
    TerminalEffect,
)
from murder.llm.harness_control.model.manual import ManualTerminalInput
from murder.llm.harness_control.model.operations import (
    ActionExpectation,
    ActionRecord,
    ControllerDecisionKind,
    DecisionRecord,
    OperationEnvelope,
    OperationStatus,
)
from murder.llm.harness_control.runtime.actuator import IntentPriority
from murder.llm.harness_control.runtime.session import (
    SessionControllerBinding,
    VerifiedHarnessControlSession,
)
from murder.runtime.sessions.contracts import (
    AcquireWriterLease,
    Correlation,
    PrincipalKind,
    PrincipalRef,
    ReleaseWriterLease,
    RequestMeta,
    WriterLeaseDenied,
    WriterLeaseGranted,
    WriterMode,
    WriteTerminalInput,
)
from murder.runtime.sessions.persistence import SessionStore, WriterLeaseRequiredError


class ManualInputPhase(Enum):
    """Manual input intentionally remains unverified after emission."""

    EMISSION_SELECTED = auto()
    AWAITING_OBSERVATION = auto()
    EMISSION_FAILED = auto()


@dataclass(frozen=True, slots=True)
class ManualInputRequest:
    text: str
    literal: bool
    append_enter: bool
    source: str


@dataclass(frozen=True, slots=True)
class ManualInputOperation:
    """Persisted state for manual input, recoverable without replaying it."""

    envelope: OperationEnvelope[ManualInputPhase]
    request: ManualInputRequest
    action_id: str


@dataclass(frozen=True, slots=True)
class ManualInputReceipt:
    operation_id: str
    action_id: str
    emission: EmissionBatchResult

    @property
    def accepted_by_terminal_transport(self) -> bool:
        """Whether tmux accepted every effect, not whether the harness acted."""

        return self.emission.ok


def lower_manual_input(action: ManualTerminalInput) -> tuple[TerminalEffect, ...]:
    """Lower direct operator intent without involving a harness workflow."""

    effects: list[TerminalEffect] = []
    if action.literal:
        effects.append(SendLiteralKeys(effect_id=str(uuid4()), text=action.text))
    else:
        effects.append(SendNamedKey(effect_id=str(uuid4()), key=action.text))
    if action.append_enter:
        effects.append(SendNamedKey(effect_id=str(uuid4()), key="Enter"))
    return tuple(effects)


async def emit_manual_input(
    control: VerifiedHarnessControlSession,
    *,
    text: str,
    literal: bool,
    append_enter: bool,
    source: str = "agent_ops.send_agent_key",
    _bypass_controller: bool = False,
    _operation_id: str | None = None,
    _action_id: str | None = None,
) -> ManualInputReceipt:
    """Durably select and serialize one human-authorized terminal input.

    ``HarnessController`` exposes public capability reconciliation only.
    Manual input is not a harness capability and cannot be truthfully verified
    as one, so this narrow runtime entry point uses the controller's owned
    journal and actuator rather than inventing an adapter workflow or a tmux
    fallback.
    """

    if not text:
        raise ValueError("manual terminal input must not be empty")

    binding = control.session_controller_binding
    if binding is not None and not _bypass_controller:
        return await _emit_bound_manual_input(
            binding,
            text=text,
            literal=literal,
            append_enter=append_enter,
            source=source,
        )

    controller = control.controller
    now = datetime.now(timezone.utc)
    operation_id = _operation_id or str(uuid4())
    action_id = _action_id or str(uuid4())
    request = ManualInputRequest(text, literal, append_enter, source)
    operation = ManualInputOperation(
        OperationEnvelope(
            operation_id=operation_id,
            capability="manual_terminal_input",
            status=OperationStatus.RUNNING,
            phase=ManualInputPhase.EMISSION_SELECTED,
            created_at=now,
            updated_at=now,
            action_history=(action_id,),
            deadline=None,
        ),
        request,
        action_id,
    )
    snapshot = controller.snapshot
    # Persist the operation and decision before lowering or terminal I/O.
    await controller._journal.record_operation(operation, snapshot)  # noqa: SLF001
    action = ManualTerminalInput(
        action_id=action_id,
        operation_id=operation_id,
        duplicate_policy=DuplicatePolicy.NEVER_AUTOMATICALLY_REPLAY,
        text=text,
        literal=literal,
        append_enter=append_enter,
        source=source,
    )
    await controller._journal.record_decision(  # noqa: SLF001
        DecisionRecord(
            operation_id=operation_id,
            observation_revision=snapshot.revision,
            phase_before=ManualInputPhase.EMISSION_SELECTED.name,
            predicate_results=(),
            selected_decision=ControllerDecisionKind.EMIT_ACTION,
            selected_action_id=action_id,
            reason="explicit user-directed raw terminal input",
            decided_at=now,
        )
    )
    record = ActionRecord(
        action_id=action_id,
        operation_id=operation_id,
        semantic_action=action,
        lowered_effects=lower_manual_input(action),
        selected_from_revision=snapshot.revision,
        requested_at=now,
        expectation=ActionExpectation(require_revision_after=snapshot.revision),
    )
    # Required crash boundary: action and all effects are durable before this
    # session's sole actuator can emit them.
    await controller._journal.prepare_action(record)  # noqa: SLF001
    emission = await controller._actuator.emit(  # noqa: SLF001
        operation_id,
        record.lowered_effects,
        priority=IntentPriority.PROMPT_SUBMISSION,
    )
    await controller._journal.record_emission(record, emission)  # noqa: SLF001
    phase = (
        ManualInputPhase.AWAITING_OBSERVATION if emission.ok else ManualInputPhase.EMISSION_FAILED
    )
    completed = replace(
        operation,
        envelope=replace(operation.envelope, phase=phase, updated_at=datetime.now(timezone.utc)),
    )
    # Transport acceptance is not semantic success. Recovery sees this durable
    # non-final operation but must never replay the manual action.
    await controller._journal.record_operation(completed, controller.snapshot)  # noqa: SLF001
    return ManualInputReceipt(operation_id, action_id, emission)


async def emit_fenced_manual_input(
    control: VerifiedHarnessControlSession,
    *,
    text: str,
    literal: bool,
    append_enter: bool,
    principal_id: str,
) -> ManualInputReceipt:
    """Acquire a short human-client lease for one serialized manual write."""

    controller = await control.ensure_session_controller()
    store = control.session_store
    if not isinstance(store, SessionStore):
        raise RuntimeError("verified control has no session writer-lease store")
    principal = PrincipalRef(kind=PrincipalKind.CLIENT, id=principal_id)
    request_id = uuid4()
    granted = await controller.acquire_writer_lease(
        AcquireWriterLease(
            meta=RequestMeta(
                request_id=request_id,
                correlation=Correlation(correlation_id=request_id),
            ),
            session_id=controller.session_id,
            mode=WriterMode.RAW_TERMINAL,
        ),
        holder=principal,
    )
    if isinstance(granted, WriterLeaseDenied):
        raise WriterLeaseRequiredError(granted.reason)
    assert isinstance(granted, WriterLeaseGranted)
    control.bind_session_controller(controller, lease=granted.lease)
    binding = control.session_controller_binding
    assert binding is not None
    try:
        return await emit_manual_input(
            control,
            text=text,
            literal=literal,
            append_enter=append_enter,
        )
    finally:
        control.unbind_session_controller(binding)
        release_id = uuid4()
        await controller.release_writer_lease(
            ReleaseWriterLease(
                meta=RequestMeta(
                    request_id=release_id,
                    correlation=Correlation(correlation_id=release_id),
                ),
                lease_id=granted.lease.lease_id,
                fence=granted.lease.fence,
            ),
            holder=principal,
        )


async def _emit_bound_manual_input(
    binding: SessionControllerBinding,
    *,
    text: str,
    literal: bool,
    append_enter: bool,
    source: str,
) -> ManualInputReceipt:
    """Translate the compatibility facade into one fenced mailbox command."""

    operation_uuid = uuid4()
    operation_id = str(operation_uuid)
    action_id = str(uuid4())
    data = _manual_input_bytes(text, literal=literal)
    if append_enter:
        data += b"\r"
    command = WriteTerminalInput(
        operation_id=operation_uuid,
        lease_id=binding.lease_id,
        fence=binding.fence,
        encoding="base64",
        data=base64.b64encode(data).decode("ascii"),
    )
    binding.control.stage_controller_manual_input(
        operation_uuid,
        text=text,
        literal=literal,
        append_enter=append_enter,
        source=source,
        action_id=action_id,
    )
    try:
        await binding.controller.execute(command, principal=binding.principal)
    except BaseException:
        binding.control.pop_controller_manual_input(operation_uuid)
        raise
    # Preserve the transitional receipt shape. Its claim remains only that the
    # physical transport accepted the bytes; the controller receipt is not a
    # harness-level acknowledgement.
    effect_id = str(uuid4())
    emission = EmissionBatchResult(
        operation_id=operation_id,
        results=(EffectEmission(effect_id, EmissionStatus.EMITTED),),
    )
    return ManualInputReceipt(operation_id, action_id, emission)


_NAMED_KEY_BYTES = {
    "Enter": b"\r",
    "Escape": b"\x1b",
    "Tab": b"\t",
    "BSpace": b"\x7f",
    "Space": b" ",
    "Up": b"\x1b[A",
    "Down": b"\x1b[B",
    "Right": b"\x1b[C",
    "Left": b"\x1b[D",
    "Home": b"\x1b[H",
    "End": b"\x1b[F",
    "PageUp": b"\x1b[5~",
    "PageDown": b"\x1b[6~",
}


def _manual_input_bytes(text: str, *, literal: bool) -> bytes:
    if literal:
        return text.encode("utf-8")
    named = _NAMED_KEY_BYTES.get(text)
    if named is not None:
        return named
    if len(text) == len("C-x") and text.startswith("C-"):
        character = text[2].upper()
        if "@" <= character <= "_":
            return bytes((ord(character) - ord("@"),))
    raise ValueError(f"named terminal key {text!r} has no raw-byte encoding")


__all__ = [
    "ManualInputOperation",
    "ManualInputPhase",
    "ManualInputReceipt",
    "ManualInputRequest",
    "emit_manual_input",
    "emit_fenced_manual_input",
    "lower_manual_input",
]
