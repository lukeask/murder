"""Harness interface and live tmux session facade."""

from __future__ import annotations

import asyncio
import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar, Literal

from murder.runtime.terminal import tmux
from murder.llm.harnesses.models import (
    HarnessModelState,
    HarnessPaneState,
    HarnessStartSpec,
    HarnessUsageStatus,
)
from murder.llm.harnesses.parsing import (
    parse_harness_model_list,
    strip_ansi,
    strip_ui_chrome,
)
from murder.llm.harnesses.capabilities import CapabilityError, HarnessCapabilities, require
from murder.llm.harnesses.transcripts import SEGMENT_TYPES, parse_frames, supports_harness
from murder.llm.harnesses.results import SimpleResult, fail_result, ok_result

_log = logging.getLogger(__name__)

ASK_RE = re.compile(r">>>\s*ASK:\s*(?P<body>.+?)(?=\n>>>|\Z)", re.DOTALL)
ANSWER_RE = re.compile(
    r">>>\s*ANSWER\[(?P<ticket>[^\]]+)\]:\s*(?P<body>.+?)(?=\n>>>|\Z)",
    re.DOTALL,
)
CHECK_RE = re.compile(r">>>\s*CHECK:\s*(?P<body>.+?)$", re.MULTILINE)
NOTE_RE = re.compile(r">>>\s*NOTE:\s*(?P<body>.+?)\n>>>\s*END\b", re.DOTALL)
DONE_RE = re.compile(r"^>>>\s*DONE[ \t]*$", re.MULTILINE)
# Used when scanning assistant segment text that may have been reflowed by the
# transcript parser (reflow joins paragraph lines with spaces, so a >>> DONE
# that was on its own pane line can appear as "... sentence. >>> DONE" in the
# segment text).  We trust the role boundary (user vs assistant) enforced by
# parse_transcript_doc, so we accept >>> DONE when it appears at the start,
# after whitespace, or after a sentence-ending character, and is followed only
# by whitespace or end-of-string (not embedded in a longer token).
_DONE_IN_SEGMENT_RE = re.compile(r"(?:^|(?<=\s))>>>\s*DONE[ \t]*(?:\n|\Z)", re.MULTILINE)
MAX_NOTE_LINES = 20

UsageCollectionMode = Literal["none", "tmux_slash", "http"]

_MODEL_REJECTION_WORD_RE = re.compile(
    r"\b("
    r"invalid|unknown|unsupported|unrecognized|unrecognised|"
    r"unavailable|not\s+available|not\s+found|no\s+such|"
    r"does\s+not\s+exist|failed\s+to\s+set|could\s+not\s+set"
    r")\b",
    re.IGNORECASE,
)
_MODEL_WORD_RE = re.compile(r"\bmodels?\b", re.IGNORECASE)


def _transcript_doc_to_turns(doc: dict[str, object]) -> list[tuple[str, str]]:
    turns: list[tuple[str, str]] = []
    segments = doc.get("segments")
    if not isinstance(segments, list):
        return turns
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        seg_type = segment.get("type")
        if seg_type == "user":
            text = segment.get("text")
            if isinstance(text, str) and text.strip():
                turns.append(("user", text))
        elif seg_type == "assistant":
            text = segment.get("text")
            if isinstance(text, str) and text.strip():
                turns.append(("assistant", text))
        elif seg_type not in SEGMENT_TYPES:
            # Other known types (tool_call, plan_update, …) are intentionally
            # absent from the flat conversation log; an *unknown* type means the
            # grammar grew a variant this projection never learned about.
            _log.warning("transcript projection: dropping unknown segment type %r", seg_type)
    return turns


class HarnessSession:
    def __init__(self, adapter: HarnessAdapter, session: str, repo_root: Path) -> None:
        self.adapter = adapter
        self.session = session
        self.repo_root = repo_root
        self._first_send_idle_gate_pending = False

    async def start(self, spec: HarnessStartSpec | None = None) -> SimpleResult[None]:
        start_spec = spec or HarnessStartSpec(
            cwd=self.repo_root,
            startup_model=self.adapter.startup_model,
            startup_effort=self.adapter.startup_effort,
        )
        # Adapters are strictly per-session (one HarnessAdapter instance backs
        # one HarnessSession). start() propagates the spec's startup model/effort
        # onto the adapter so that startup_cmd() and the "model already selected"
        # check below read a single source of truth. Do not share an adapter
        # across sessions — this write would otherwise leak one session's model
        # into the next.
        if start_spec.startup_model is not None:
            self.adapter.startup_model = start_spec.startup_model
        if start_spec.startup_effort is not None:
            self.adapter.startup_effort = start_spec.startup_effort
        self.adapter.additional_workspace_dirs = tuple(
            Path(path) for path in start_spec.additional_workspace_dirs
        )
        await tmux.create_session(
            self.session,
            start_spec.cwd,
            self.adapter.startup_cmd(start_spec.cwd),
        )

        ready = await self._wait_startup_ready(start_spec)
        if not ready.ok:
            return ready
        configured = await self._configure_started_session(start_spec)
        if not configured.ok:
            return configured
        self._first_send_idle_gate_pending = True
        return ok_result()

    def require_first_send_idle_gate(self) -> None:
        self._first_send_idle_gate_pending = True

    async def _wait_startup_ready(self, start_spec: HarnessStartSpec) -> SimpleResult[None]:
        attempts = max(1, int(start_spec.ready_timeout_s / start_spec.poll_interval_s))
        for _ in range(attempts):
            try:
                pane = await tmux.capture_pane(self.session, lines=120)
            except tmux.TmuxError as e:
                return fail_result(f"Session lost during startup: {e}")
            if self.adapter.is_ready(pane):
                break
            await asyncio.sleep(start_spec.poll_interval_s)
        else:
            return fail_result(f"Harness not ready in time: session={self.session}")
        return ok_result()

    async def _configure_started_session(self, start_spec: HarnessStartSpec) -> SimpleResult[None]:
        desired_model = start_spec.startup_model or self.adapter.startup_model
        desired_effort = start_spec.startup_effort or self.adapter.startup_effort
        if (
            desired_model
            and desired_effort is None
            and self.adapter.supported_efforts
            and self.adapter.assume_default_effort_when_omitted
        ):
            desired_effort = self.adapter.default_effort

        init_result = await self.initialize_defaults(start_spec)
        if not init_result.ok:
            return init_result
        idle_result = await self.wait_idle(timeout_s=15.0)
        if not idle_result.ok:
            return idle_result

        if desired_model:
            launched_model = start_spec.startup_model or self.adapter.startup_model
            startup_already_selected = (
                self.adapter.startup_model_selects_runtime_model
                and desired_model == launched_model
            )
            # A startup --model flag (codex) selects the model but carries no
            # reasoning effort, so when a *non-default* effort is wanted we still
            # run the runtime selection even though the model is already in place.
            # set_model() short-circuits the default-effort case, so this only
            # drives the picker when effort genuinely needs changing.
            needs_runtime_effort = (
                desired_effort is not None
                and desired_effort != self.adapter.default_effort
            )
            if not startup_already_selected or needs_runtime_effort:
                model_result = await self.set_model(desired_model, desired_effort)
                if not model_result.ok:
                    return model_result
                idle_result = await self.wait_idle(timeout_s=15.0)
                if not idle_result.ok:
                    return idle_result
        return ok_result()

    async def wait_ready(self, timeout_s: float = 240.0) -> SimpleResult[None]:
        attempts = max(1, int(timeout_s / 0.4))
        for _ in range(attempts):
            try:
                pane = await tmux.capture_pane(self.session, lines=120)
            except tmux.TmuxError as e:
                return fail_result(f"Session lost during ready-wait: {e}")
            if self.adapter.is_ready(pane):
                return ok_result()
            await asyncio.sleep(0.4)
        return fail_result(f"Harness not ready in time: session={self.session}")

    async def wait_idle(self, timeout_s: float = 30.0) -> SimpleResult[None]:
        attempts = max(1, int(timeout_s / 0.4))
        for _ in range(attempts):
            try:
                pane = await tmux.capture_pane(self.session, lines=120)
            except tmux.TmuxError as e:
                return fail_result(f"Session lost during idle-wait: {e}")
            if self.adapter.is_idle(pane):
                return ok_result()
            await asyncio.sleep(0.4)
        return fail_result(f"Harness not idle in time: session={self.session}")

    def _pane_is_awaiting_input(self, pane_text: str) -> bool:
        input_ready = self.adapter.is_input_ready(pane_text)
        if input_ready is not None:
            return input_ready
        if self.adapter.has_transcript_parser():
            return self.adapter.parse_transcript_doc(pane_text).get("state") == "awaiting_input"
        return self.adapter.is_idle(pane_text)

    async def wait_input_ready(
        self,
        timeout_s: float = 30.0,
        *,
        stable_polls: int = 2,
    ) -> SimpleResult[None]:
        attempts = max(1, int(timeout_s / 0.4))
        needed = max(1, stable_polls)
        consecutive = 0
        for _ in range(attempts):
            try:
                pane = await tmux.capture_pane(self.session, lines=120)
            except tmux.TmuxError as e:
                return fail_result(f"Session lost during input-ready-wait: {e}")
            if self._pane_is_awaiting_input(pane):
                consecutive += 1
                if consecutive >= needed:
                    return ok_result()
            else:
                consecutive = 0
            await asyncio.sleep(0.4)
        return fail_result(f"Harness not awaiting input in time: session={self.session}")

    async def initialize_defaults(self, spec: HarnessStartSpec) -> SimpleResult[None]:
        return await self.adapter.initialize_defaults(self.session, spec)

    def status_from_pane(self, pane_text: str) -> HarnessPaneState:
        return HarnessPaneState(
            ready=self.adapter.is_ready(pane_text),
            idle=self.adapter.is_idle(pane_text),
            busy=self.adapter.is_busy(pane_text),
        )

    async def send_prompt(self, prompt: str) -> SimpleResult[None]:
        if self._first_send_idle_gate_pending:
            input_ready = await self.wait_input_ready(timeout_s=15.0)
            if not input_ready.ok:
                return input_ready
        try:
            delivered = await self.adapter.send_prompt(self.session, prompt)
        except Exception as e:
            return fail_result(f"Harness prompt delivery failed: {e}")
        if not delivered.ok:
            return delivered
        self._first_send_idle_gate_pending = False
        return ok_result()

    async def set_model(self, model: str, effort: str | None = None) -> SimpleResult[None]:
        # A model selected by the launch --model flag already runs at the
        # harness's default effort, so requesting that same model with no (or
        # the default) effort is a no-op. A non-default effort still has to be
        # applied via the adapter's runtime picker.
        if (
            self.adapter.startup_model_selects_runtime_model
            and self.adapter.startup_model == model
            and (effort is None or effort == self.adapter.default_effort)
        ):
            return ok_result()
        selected = await self.adapter.set_model(self.session, model, effort=effort)
        if selected:
            return ok_result()
        effort_msg = f" with effort {effort!r}" if effort else ""
        return fail_result(
            f"{self.adapter.kind} failed to select runtime model {model!r}{effort_msg} "
            f"(startup_model={self.adapter.startup_model!r}, "
            f"startup_selects_runtime={self.adapter.startup_model_selects_runtime_model})"
        )

    async def request_usage_status(self) -> SimpleResult[None]:
        requested = await self.adapter.request_usage_status(self.session)
        if requested:
            return ok_result()
        return fail_result(f"{self.adapter.kind} does not support usage/status reporting")

    async def collect_usage_status(self) -> SimpleResult[HarnessUsageStatus]:
        return await self.adapter.collect_usage_status(self.session)

    async def collect_available_models(self) -> SimpleResult[list[tuple[str, str]]]:
        return await self.adapter.collect_available_models(self.session)

    async def collect_active_model_state(self) -> SimpleResult[HarnessModelState]:
        pane = await tmux.capture_pane(self.session, lines=200)
        state = self.adapter.parse_active_model_state(pane)
        if state is None or (state.model is None and state.effort is None):
            return fail_result(f"{self.adapter.kind} active model state was not visible")
        return ok_result(state)

    async def probe_invalid_model(self, model: str) -> SimpleResult[None]:
        return await self.adapter.probe_invalid_model(self.session, model)

    async def interrupt(self) -> SimpleResult[None]:
        await self.adapter.interrupt(self.session)
        return ok_result()


class HarnessAdapter(ABC):
    kind: ClassVar[str]
    crow_system_prompt: ClassVar[str]
    available_startup_models: ClassVar[list[tuple[str, str]]] = []
    model_list_command: ClassVar[str | None] = "/models"
    model_list_capture_delay_s: ClassVar[float] = 0.8
    model_selection_command_template: ClassVar[str | None] = "/model {model}"
    model_selection_capture_delay_s: ClassVar[float] = 0.8
    supported_efforts: ClassVar[tuple[str, ...]] = ()
    default_effort: ClassVar[str] = "medium"
    assume_default_effort_when_omitted: ClassVar[bool] = True
    usage_collection_mode: ClassVar[UsageCollectionMode] = "none"
    supports_subagents: ClassVar[bool] = False
    cheapest_subagent_model: ClassVar[str | None] = None
    startup_model_selects_runtime_model: ClassVar[bool] = False

    def __init__(
        self,
        startup_model: str | None = None,
        startup_effort: str | None = None,
    ) -> None:
        self.startup_model = startup_model
        self.startup_effort = startup_effort
        # The murder-owned system prompt injected as this session's first user
        # message. Set by the agent at start() so markerless transcript parsers
        # (cursor, pi) can strip it instead of mislabelling it as chat turns.
        self.system_prompt: str | None = None
        self.additional_workspace_dirs: tuple[Path, ...] = ()

    @classmethod
    def declared_capabilities(cls) -> HarnessCapabilities:
        """Derive capability flags from adapter class vars (registry source of truth)."""
        return HarnessCapabilities(
            usage_reporting=cls.usage_collection_mode != "none",
            model_discovery=cls.model_list_command is not None,
            model_selection=cls.model_selection_command_template is not None,
            pane_state_reading=True,
            transcript_access=supports_harness(cls.kind),
            startup_interrupt_continue=True,
            supports_subagents=cls.supports_subagents,
            cheapest_subagent_model=cls.cheapest_subagent_model,
        )

    def capabilities(self) -> HarnessCapabilities:
        return self.declared_capabilities()

    def attach(self, session: str, repo_root: Path) -> HarnessSession:
        return HarnessSession(self, session, repo_root)

    @abstractmethod
    def startup_cmd(self, cwd: Path) -> list[str]: ...

    @abstractmethod
    def is_ready(self, pane_text: str) -> bool: ...

    @abstractmethod
    def is_idle(self, pane_text: str) -> bool: ...

    @abstractmethod
    def is_busy(self, pane_text: str) -> bool: ...

    def is_input_ready(self, pane_text: str) -> bool | None:
        del pane_text
        return None

    async def initialize_defaults(self, session: str, spec: HarnessStartSpec) -> SimpleResult[None]:
        del session, spec
        return ok_result()

    async def send_prompt(self, session: str, prompt: str) -> SimpleResult[None]:
        await tmux.send_keys(session, prompt, literal=True, enter=True)
        return ok_result()

    async def set_model(self, session: str, model: str, *, effort: str | None = None) -> bool:
        del session, model, effort
        return False

    async def request_model_selection(
        self,
        session: str,
        model: str,
        *,
        effort: str | None = None,
    ) -> bool:
        del effort
        if self.model_selection_command_template is None:
            return False
        await tmux.send_keys(
            session,
            self.model_selection_command_template.format(model=model),
            literal=True,
            enter=True,
        )
        await asyncio.sleep(self.model_selection_capture_delay_s)
        return True

    def detects_model_rejection(self, pane_text: str, model: str) -> bool:
        clean = strip_ansi(pane_text)
        model_at = clean.lower().find(model.lower())
        if model_at >= 0:
            window = clean[max(0, model_at - 240) : model_at + len(model) + 240]
            return bool(_MODEL_REJECTION_WORD_RE.search(window))
        tail = clean[-1200:]
        return bool(_MODEL_REJECTION_WORD_RE.search(tail) and _MODEL_WORD_RE.search(tail))

    async def probe_invalid_model(self, session: str, model: str) -> SimpleResult[None]:
        try:
            require(self.capabilities(), "model_selection")
        except CapabilityError as e:
            return fail_result(str(e))
        requested = await self.request_model_selection(session, model)
        if not requested:
            return fail_result(f"{self.kind} does not support runtime model selection")
        pane = await tmux.capture_pane(session, lines=200)
        if self.detects_model_rejection(pane, model):
            return ok_result()
        return fail_result(f"{self.kind} did not reject invalid model selection for {model!r}")

    async def request_usage_status(self, session: str) -> bool:
        del session
        return False

    async def collect_usage_status(self, session: str) -> SimpleResult[HarnessUsageStatus]:
        del session
        try:
            require(self.capabilities(), "usage_reporting")
        except CapabilityError as e:
            return fail_result(str(e))
        return fail_result(f"{self.kind} does not support structured usage/status reporting")

    async def request_model_list(self, session: str) -> bool:
        if self.model_list_command is None:
            return False
        await tmux.send_keys(session, self.model_list_command, literal=True, enter=True)
        await asyncio.sleep(self.model_list_capture_delay_s)
        return True

    async def collect_available_models(self, session: str) -> SimpleResult[list[tuple[str, str]]]:
        requested = await self.request_model_list(session)
        if not requested:
            return fail_result(f"{self.kind} does not support /models discovery")
        pane = await tmux.capture_pane(session, lines=200)
        models = parse_harness_model_list(pane)
        if not models:
            return fail_result(f"{self.kind} /models did not expose any model choices")
        return ok_result(models)

    def parse_active_model_state(self, pane_text: str) -> HarnessModelState | None:
        del pane_text
        return None

    def graceful_exit_command(self) -> str | None:
        """Return the command to send for a graceful exit, or None if unsupported."""
        return None

    def extract_resume_session_id(self, pane_text: str) -> str | None:
        """Parse the harness's 'to resume this session' output and return the session id."""
        del pane_text
        return None

    @abstractmethod
    def extract_last_message(self, pane_text: str) -> str | None: ...

    def has_transcript_parser(self) -> bool:
        return supports_harness(self.kind)

    def parse_transcript_doc(self, pane_text: str) -> dict[str, object]:
        if not supports_harness(self.kind):
            return {
                "harness": self.kind,
                "state": "working",
                "condensed": None,
                "segments": [],
            }
        return parse_frames(self.kind, [pane_text], system_prompt=self.system_prompt)

    def parse_transcript(self, pane_text: str) -> list[tuple[str, str]]:
        """Best-effort visible user/assistant turns projected from TranscriptDoc.

        Returns the *full* visible transcript on every call — never deltas;
        :func:`murder.state.persistence.conversation.merge_transcript` reconciles
        successive parses. This remains a compatibility projection for the
        persisted conversation log, whose storage model is still flat turns.
        """
        return _transcript_doc_to_turns(self.parse_transcript_doc(pane_text))

    def detect_ask(self, pane_text: str) -> str | None:
        m = ASK_RE.search(strip_ui_chrome(pane_text))
        return m.group("body").strip() if m else None

    def detect_asks(self, pane_text: str) -> list[str]:
        clean = strip_ui_chrome(pane_text)
        return [m.group("body").strip() for m in ASK_RE.finditer(clean)]

    def detect_answers(self, pane_text: str) -> list[tuple[str, str]]:
        clean = strip_ui_chrome(pane_text)
        return [
            (
                m.group("ticket").strip(),
                (m.group("body").strip().splitlines() or [""])[0].strip(),
            )
            for m in ANSWER_RE.finditer(clean)
        ]

    def detect_checks(self, pane_text: str) -> list[str]:
        clean = strip_ui_chrome(pane_text)
        return [m.group("body").strip() for m in CHECK_RE.finditer(clean)]

    def detect_notes(self, pane_text: str) -> list[str]:
        clean = strip_ui_chrome(pane_text)
        notes: list[str] = []
        for match in NOTE_RE.finditer(clean):
            lines = match.group("body").strip().splitlines()
            notes.append("\n".join(lines[:MAX_NOTE_LINES]).strip())
        return [note for note in notes if note]

    def detect_done(self, pane_text: str) -> bool:
        """Return True iff the assistant (crow) emitted ``>>> DONE``.

        Source-aware: ``>>> DONE`` that appears in user/system content (the
        pasted startup brief or a follow-up user message) must never trigger
        completion — only a crow-authored ``>>> DONE`` counts.

        When a transcript parser is available the search is restricted to
        assistant-role segments, so the startup brief's ``>>> DONE`` example
        (pasted as a user turn) is excluded.  ``_DONE_IN_SEGMENT_RE`` is used
        instead of the stricter ``DONE_RE`` because the transcript reflow may
        join a standalone ``>>> DONE`` line onto the preceding sentence via a
        space, making the ``^`` anchor in ``DONE_RE`` miss it.

        For harnesses without a transcript parser the search falls back to the
        full pane after stripping UI chrome (original behaviour).
        """
        if self.has_transcript_parser():
            doc = self.parse_transcript_doc(pane_text)
            return any(
                isinstance(s, dict)
                and s.get("type") == "assistant"
                and isinstance(s.get("text"), str)
                and bool(_DONE_IN_SEGMENT_RE.search(s["text"]))
                for s in doc.get("segments", [])
            )
        return bool(DONE_RE.search(strip_ui_chrome(pane_text)))

    async def interrupt(self, session: str) -> None:
        """Stop an in-flight generation. Override per harness (see plan Obj 4)."""
        await tmux.interrupt(session)

    async def interrupt_generation(self, session: str) -> None:
        """Send Escape — shared by interactive CLIs that document esc-to-interrupt."""
        await tmux.send_keys(session, "Escape", literal=False, enter=False)
