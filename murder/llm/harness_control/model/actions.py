"""Semantic action and physical terminal-effect values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum, auto

ActionId = str
EffectId = str
OperationId = str


class DuplicatePolicy(Enum):
    REPLAY_SAFE = auto()
    REPLAY_SAFE_WHILE_PRECONDITION_HOLDS = auto()
    SAFE_BEFORE_COMMIT = auto()
    AMBIGUOUS_AFTER_EMISSION = auto()
    NEVER_AUTOMATICALLY_REPLAY = auto()


class InputProvenance(Enum):
    MURDER_CONTEXT_BLOCK = auto()
    USER_PASTE_BLOCK = auto()
    USER_TYPED = auto()


@dataclass(frozen=True, slots=True)
class InputChunk:
    text: str
    provenance: InputProvenance
    stable_chunk_id: str


@dataclass(frozen=True, slots=True)
class DelayProfile:
    min_delay_ms: float
    max_delay_ms: float
    distribution: str = "triangular"

    def __post_init__(self) -> None:
        if self.min_delay_ms < 0 or self.max_delay_ms < self.min_delay_ms:
            raise ValueError("invalid delay profile")


FAST_HUMANIZED_TYPING = DelayProfile(1.0, 5.0)


@dataclass(frozen=True, slots=True)
class SemanticAction:
    action_id: ActionId
    operation_id: OperationId
    duplicate_policy: DuplicatePolicy


@dataclass(frozen=True, slots=True)
class InsertPromptPayload(SemanticAction):
    chunks: tuple[InputChunk, ...]
    expected_fingerprint: str


@dataclass(frozen=True, slots=True)
class CommitPromptSubmission(SemanticAction):
    pass


@dataclass(frozen=True, slots=True)
class ClearComposer(SemanticAction):
    pass


@dataclass(frozen=True, slots=True)
class DismissOverlay(SemanticAction):
    overlay_kind: str | None


@dataclass(frozen=True, slots=True)
class SendInterrupt(SemanticAction):
    pass


class QuestionAnswerMode(Enum):
    SINGLE = auto()
    MULTIPLE = auto()
    CUSTOM = auto()
    DECLINE = auto()


@dataclass(frozen=True, slots=True)
class QuestionChoiceSelection:
    """Semantic identity for one selected menu choice, never a screen row."""

    stable_choice_id: str | None
    label: str

    def __post_init__(self) -> None:
        if not self.stable_choice_id and not self.label.strip():
            raise ValueError("a question choice needs an id or a non-empty label")


@dataclass(frozen=True, slots=True)
class AnswerQuestion(SemanticAction):
    question_id_hint: str | None
    mode: QuestionAnswerMode
    selections: tuple[QuestionChoiceSelection, ...] = ()
    custom_answer: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class AnswerPermission(SemanticAction):
    request_id_hint: str | None
    response_id: str | None
    response_label: str | None


@dataclass(frozen=True, slots=True)
class SelectModel(SemanticAction):
    model_id: str
    provider: str | None = None
    effort: str | None = None
    context_mode: str | None = None
    fast_enabled: bool | None = None
    max_mode_enabled: bool | None = None
    thinking_enabled: bool | None = None
    run_mode: str | None = None


@dataclass(frozen=True, slots=True)
class OpenModelPicker(SemanticAction):
    """Navigate to the observed model picker without selecting anything.

    Opening a picker is intentionally distinct from choosing or confirming a
    model.  It is a reversible surface transition whose later frame must show
    the picker before a ``SelectModel`` action can be lowered.
    """

    filter_text: str | None = None
    edit_parameters: bool = False


@dataclass(frozen=True, slots=True)
class OpenResumePicker(SemanticAction):
    """Open the harness session-resume picker from an observed safe composer."""


@dataclass(frozen=True, slots=True)
class ConfigureResumePicker(SemanticAction):
    """Configure a freshly opened resume picker's search/filter/sort state."""

    search_text: str
    filter_mode: str
    sort_mode: str


@dataclass(frozen=True, slots=True)
class NavigateModelPicker(SemanticAction):
    """Move one row in a currently observed model picker without confirming."""

    direction: str

    def __post_init__(self) -> None:
        if self.direction not in {"up", "down"}:
            raise ValueError("model-picker direction must be 'up' or 'down'")


@dataclass(frozen=True, slots=True)
class ConfigureSessionSettings(SemanticAction):
    """Apply requested interactive settings; later chrome must verify them."""

    run_mode: str | None = None
    fast_enabled: bool | None = None


@dataclass(frozen=True, slots=True)
class RestoreComposer(SemanticAction):
    pass


@dataclass(frozen=True, slots=True)
class RequestUsage(SemanticAction):
    """Request a fresh usage observation from a harness-specific source."""

    preferred_source: str | None = None


@dataclass(frozen=True, slots=True)
class TerminalEffect:
    effect_id: EffectId


@dataclass(frozen=True, slots=True)
class SendLiteralKeys(TerminalEffect):
    text: str
    inter_key_delay: DelayProfile | None = None


@dataclass(frozen=True, slots=True)
class PasteBuffer(TerminalEffect):
    text: str


@dataclass(frozen=True, slots=True)
class SendNamedKey(TerminalEffect):
    key: str


@dataclass(frozen=True, slots=True)
class SleepEffect(TerminalEffect):
    duration: timedelta


class EmissionStatus(Enum):
    PENDING = auto()
    EMITTED = auto()
    FAILED = auto()


@dataclass(frozen=True, slots=True)
class EffectEmission:
    effect_id: EffectId
    status: EmissionStatus
    error: str | None = None


@dataclass(frozen=True, slots=True)
class EmissionBatchResult:
    operation_id: OperationId
    results: tuple[EffectEmission, ...]

    @property
    def ok(self) -> bool:
        return all(result.status is EmissionStatus.EMITTED for result in self.results)
