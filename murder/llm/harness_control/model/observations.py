"""Narrow shared observations and explicit knowledge states."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import Generic, TypeVar

from murder.llm.harness_control.model.evidence import EvidenceRef, HarnessId

T = TypeVar("T")


class Knowledge(Enum):
    PRESENT = auto()
    ABSENT = auto()
    UNKNOWN = auto()
    UNSUPPORTED = auto()
    CONTRADICTED = auto()
    STALE = auto()


@dataclass(frozen=True, slots=True, order=True)
class ObservationRevision:
    pane_epoch: int
    capture_sequence: int
    semantic_sequence: int

    def __post_init__(self) -> None:
        if min(self.pane_epoch, self.capture_sequence, self.semantic_sequence) < 0:
            raise ValueError("observation revisions cannot be negative")


@dataclass(frozen=True, slots=True)
class Observed(Generic[T]):
    knowledge: Knowledge
    value: T | None
    evidence: tuple[EvidenceRef, ...]
    observed_at: datetime
    revision: ObservationRevision
    explanation: str | None = None

    def __post_init__(self) -> None:
        if (self.knowledge is Knowledge.PRESENT) != (self.value is not None):
            raise ValueError("only PRESENT observations may carry a value")

    @classmethod
    def present(
        cls,
        value: T,
        *,
        evidence: tuple[EvidenceRef, ...],
        observed_at: datetime,
        revision: ObservationRevision,
        explanation: str | None = None,
    ) -> Observed[T]:
        return cls(Knowledge.PRESENT, value, evidence, observed_at, revision, explanation)

    @classmethod
    def without_value(
        cls,
        knowledge: Knowledge,
        *,
        evidence: tuple[EvidenceRef, ...] = (),
        observed_at: datetime,
        revision: ObservationRevision,
        explanation: str | None = None,
    ) -> Observed[T]:
        if knowledge is Knowledge.PRESENT:
            raise ValueError("use present() for a present observation")
        return cls(knowledge, None, evidence, observed_at, revision, explanation)


class SurfaceKind(Enum):
    COMPOSER = auto()
    TRANSCRIPT = auto()
    MODEL_PICKER = auto()
    USAGE_PANEL = auto()
    STATUS_PANEL = auto()
    PERMISSION_DIALOG = auto()
    QUESTION_PICKER = auto()
    RESUME_PICKER = auto()
    CONTEXT_PANEL = auto()
    TRUST_DIALOG = auto()
    LOGIN_DIALOG = auto()
    SHELL = auto()
    UNKNOWN_OVERLAY = auto()


class ComposerActionability(Enum):
    ACTIONABLE = auto()
    VISIBLE_NOT_ACTIONABLE = auto()
    HIDDEN = auto()
    DISABLED = auto()
    UNKNOWN = auto()


class GenerationPhase(Enum):
    IDLE = auto()
    STARTING = auto()
    THINKING = auto()
    RUNNING_TOOL = auto()
    STREAMING = auto()
    COMPACTING = auto()
    INTERRUPTING = auto()
    STOPPED = auto()
    COMPLETE = auto()
    UNKNOWN = auto()


class ModalKind(Enum):
    MODEL_PICKER = auto()
    USAGE = auto()
    STATUS = auto()
    PERMISSION = auto()
    QUESTION = auto()
    RESUME = auto()
    CONTEXT = auto()
    TRUST = auto()
    LOGIN = auto()
    UNKNOWN = auto()


@dataclass(frozen=True, slots=True)
class SurfaceState:
    primary: SurfaceKind
    visible: frozenset[SurfaceKind]
    focused: SurfaceKind | None
    blocks_composer_observation: bool
    blocks_composer_input: bool


@dataclass(frozen=True, slots=True)
class ComposerState:
    text: str | None
    normalized_text: str | None
    content_fingerprint: str | None
    cursor_visible: bool | None
    focused: bool | None
    actionability: ComposerActionability
    is_partial: bool | None
    accepts_submission: bool | None
    queued_follow_up_text: str | None = None
    attachments: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True, slots=True)
class GenerationState:
    phase: GenerationPhase
    active: bool | None
    spinner_visible: bool | None
    elapsed: timedelta | None
    token_count: int | None
    current_tool: str | None
    interruption_requested: bool | None = None
    compaction_state: str | None = None


@dataclass(frozen=True, slots=True)
class TurnRef:
    stable_id: str
    role: str


@dataclass(frozen=True, slots=True)
class TranscriptTailState:
    last_user_turn: TurnRef | None
    last_assistant_turn: TurnRef | None
    visible_user_fingerprints: tuple[str, ...]
    assistant_streaming: bool | None
    assistant_completed: bool | None
    latest_text_hash: str | None
    transcript_revision: int


@dataclass(frozen=True, slots=True)
class ModalState:
    kind: ModalKind
    title: str | None
    selected_index: int | None
    option_count: int | None
    dismissible_with_escape: bool | None
    blocks_input: bool


@dataclass(frozen=True, slots=True)
class ChoiceState:
    stable_choice_id: str | None
    label: str
    description: str | None = None
    number: int | None = None
    shortcut: str | None = None
    selected: bool | None = None
    highlighted: bool | None = None
    checked: bool | None = None
    disabled: bool | None = None
    current: bool | None = None


@dataclass(frozen=True, slots=True)
class PermissionRequestState:
    request_id_hint: str | None
    tool_name: str | None
    command: str | None
    description: str | None
    choices: tuple[ChoiceState, ...]
    selected_choice: str | None
    risk_attributes: frozenset[str]
    # A harness-rendered confirmation bound to a response identifier.  This is
    # deliberately distinct from a dialog disappearing: a vanished modal does
    # not establish that an unsafe approval was accepted.
    acknowledged_response_id: str | None = None


@dataclass(frozen=True, slots=True)
class QuestionState:
    """Normalized control view; adapters retain the richer menu evidence."""

    question_id_hint: str | None
    prompt_text: str | None
    choices: tuple[ChoiceState, ...]
    selection_mode: str | None
    active_tab: str | None
    visible_tabs: tuple[str, ...]
    allow_custom_answer: bool | None
    custom_answer_text: str | None
    submit_label: str | None
    decline_label: str | None
    answered_summary: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ModelState:
    model_id: str
    effort: str | None
    display_name: str | None
    provider: str | None = None


@dataclass(frozen=True, slots=True)
class ModelConfigurationState:
    """Configuration is distinct from independently read active runtime model."""

    available: tuple[ChoiceState, ...]
    highlighted_model_id: str | None
    selected_model_id: str | None
    configured_model_id: str | None
    pending_changes: bool | None
    parameters: tuple[tuple[str, str | bool | None], ...] = ()


@dataclass(frozen=True, slots=True)
class UsageWindow:
    name: str
    percent_used: float | None
    resets_at: datetime | None
    reset_text: str | None


@dataclass(frozen=True, slots=True)
class UsageState:
    model: str | None
    plan: str | None
    windows: tuple[UsageWindow, ...]
    freshness: str
    source_surface: SurfaceKind | None
    advisory_text: str | None
    session_totals: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class ToolInteraction:
    tool_name: str | None
    command: str | None
    paths_read: tuple[str, ...]
    paths_written: tuple[str, ...]
    status: str | None
    started_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ToolActivityState:
    active: tuple[ToolInteraction, ...]
    recent: tuple[ToolInteraction, ...]


@dataclass(frozen=True, slots=True)
class ObservationHealth:
    parser_status: str
    splice_reset_count: int = 0
    contradiction_count: int = 0
    stale_revision_count: int = 0
    unrecognized_surface_count: int = 0
    recent_control_failures: int = 0
    degraded: bool = False
    requires_escalation: bool = False


@dataclass(frozen=True, slots=True)
class AuthoritativeFacts:
    intended_prompts: tuple[dict[str, object], ...] = ()
    emitted_actions: tuple[str, ...] = ()
    persisted_turns: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True, slots=True)
class ObservationSnapshot:
    revision: ObservationRevision
    harness_id: HarnessId
    captured_at: datetime
    surface: Observed[SurfaceState]
    composer: Observed[ComposerState]
    generation: Observed[GenerationState]
    transcript_tail: Observed[TranscriptTailState]
    modal: Observed[ModalState]
    question: Observed[QuestionState]
    permission_request: Observed[PermissionRequestState]
    active_model: Observed[ModelState]
    model_configuration: Observed[ModelConfigurationState]
    usage: Observed[UsageState]
    tool_activity: Observed[ToolActivityState]
    health: ObservationHealth
    facts: AuthoritativeFacts = field(default_factory=AuthoritativeFacts)


@dataclass(frozen=True, slots=True)
class ObservationDelta:
    """A partial projection plus durable events that do not fit snapshot state."""

    updates: dict[str, Observed[object]]
    evidence_refs: tuple[EvidenceRef, ...] = ()
    semantic_events: tuple[dict[str, object], ...] = ()
    diagnostics: tuple[str, ...] = ()


def unknown_snapshot(
    harness_id: HarnessId, *, captured_at: datetime, revision: ObservationRevision | None = None
) -> ObservationSnapshot:
    """Construct a valid empty control snapshot without pretending absence."""

    current = revision or ObservationRevision(0, 0, 0)

    def unknown() -> Observed[object]:
        return Observed.without_value(
            Knowledge.UNKNOWN,
            observed_at=captured_at,
            revision=current,
            explanation="not observed yet",
        )

    return ObservationSnapshot(
        revision=current,
        harness_id=harness_id,
        captured_at=captured_at,
        surface=unknown(),  # type: ignore[arg-type]
        composer=unknown(),  # type: ignore[arg-type]
        generation=unknown(),  # type: ignore[arg-type]
        transcript_tail=unknown(),  # type: ignore[arg-type]
        modal=unknown(),  # type: ignore[arg-type]
        question=unknown(),  # type: ignore[arg-type]
        permission_request=unknown(),  # type: ignore[arg-type]
        active_model=unknown(),  # type: ignore[arg-type]
        model_configuration=unknown(),  # type: ignore[arg-type]
        usage=unknown(),  # type: ignore[arg-type]
        tool_activity=unknown(),  # type: ignore[arg-type]
        health=ObservationHealth(parser_status="unobserved"),
    )
