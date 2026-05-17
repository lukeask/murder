"""Harness interface and live tmux session facade."""

from __future__ import annotations

import asyncio
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar, Literal

from murder import tmux
from murder.harnesses.models import (
    HarnessPaneState,
    HarnessStartSpec,
    HarnessUsageStatus,
)
from murder.harnesses.parsing import (
    parse_harness_model_list,
    parse_prompt_marker_transcript,
    strip_ansi,
    strip_ui_chrome,
)
from murder.harnesses.results import SimpleResult, fail_result, ok_result

ASK_RE = re.compile(r">>>\s*ASK:\s*(?P<body>.+?)(?=\n>>>|\Z)", re.DOTALL)
CHECK_RE = re.compile(r">>>\s*CHECK:\s*(?P<body>.+?)$", re.MULTILINE)
NOTE_RE = re.compile(r">>>\s*NOTE:\s*(?P<body>.+?)\n>>>\s*END\b", re.DOTALL)
DONE_RE = re.compile(r">>>\s*DONE\b")
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

    async def _wait_startup_ready(
        self, start_spec: HarnessStartSpec
    ) -> SimpleResult[None]:
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

    async def _configure_started_session(
        self, start_spec: HarnessStartSpec
    ) -> SimpleResult[None]:
        desired_model = start_spec.startup_model or self.adapter.startup_model
        if desired_model:
            model_result = await self.set_model(desired_model)
            if not model_result.ok:
                return model_result
            idle_result = await self.wait_idle(timeout_s=15.0)
            if not idle_result.ok:
                return idle_result

        init_result = await self.initialize_defaults(start_spec)
        if not init_result.ok:
            return init_result
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
            idle = await self.wait_idle(timeout_s=15.0)
            if not idle.ok:
                return idle
        await self.adapter.send_prompt(self.session, prompt)
        self._first_send_idle_gate_pending = False
        return ok_result()

    async def set_model(self, model: str) -> SimpleResult[None]:
        selected = await self.adapter.set_model(self.session, model)
        if selected:
            return ok_result()
        return fail_result(
            f"{self.adapter.kind} does not support runtime model selection"
        )

    async def request_usage_status(self) -> SimpleResult[None]:
        requested = await self.adapter.request_usage_status(self.session)
        if requested:
            return ok_result()
        return fail_result(
            f"{self.adapter.kind} does not support usage/status reporting"
        )

    async def collect_usage_status(self) -> SimpleResult[HarnessUsageStatus]:
        return await self.adapter.collect_usage_status(self.session)

    async def collect_available_models(self) -> SimpleResult[list[tuple[str, str]]]:
        return await self.adapter.collect_available_models(self.session)

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
    usage_collection_mode: ClassVar[UsageCollectionMode] = "none"

    # Inputs for the default transcript parser (see parse_transcript). Leave the
    # markers empty for a harness whose UI doesn't echo prompts behind a simple
    # marker; such a harness either overrides parse_transcript or has no parsed
    # transcript yet (the raw pane mirror remains available in the TUI).
    transcript_prompt_markers: ClassVar[tuple[str, ...]] = ()
    transcript_drop_substrings: ClassVar[tuple[str, ...]] = ()

    def __init__(self, startup_model: str | None = None) -> None:
        self.startup_model = startup_model

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

    async def initialize_defaults(
        self, session: str, spec: HarnessStartSpec
    ) -> SimpleResult[None]:
        del session, spec
        return ok_result()

    async def send_prompt(self, session: str, prompt: str) -> None:
        await tmux.send_keys(session, prompt, literal=True, enter=True)

    async def set_model(self, session: str, model: str) -> bool:
        del session, model
        return False

    async def request_model_selection(self, session: str, model: str) -> bool:
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
        return bool(
            _MODEL_REJECTION_WORD_RE.search(tail) and _MODEL_WORD_RE.search(tail)
        )

    async def probe_invalid_model(
        self, session: str, model: str
    ) -> SimpleResult[None]:
        requested = await self.request_model_selection(session, model)
        if not requested:
            return fail_result(f"{self.kind} does not support runtime model selection")
        pane = await tmux.capture_pane(session, lines=200)
        if self.detects_model_rejection(pane, model):
            return ok_result()
        return fail_result(
            f"{self.kind} did not reject invalid model selection for {model!r}"
        )

    async def request_usage_status(self, session: str) -> bool:
        del session
        return False

    async def collect_usage_status(
        self, session: str
    ) -> SimpleResult[HarnessUsageStatus]:
        del session
        return fail_result(
            f"{self.kind} does not support structured usage/status reporting"
        )

    async def request_model_list(self, session: str) -> bool:
        if self.model_list_command is None:
            return False
        await tmux.send_keys(session, self.model_list_command, literal=True, enter=True)
        await asyncio.sleep(self.model_list_capture_delay_s)
        return True

    async def collect_available_models(
        self, session: str
    ) -> SimpleResult[list[tuple[str, str]]]:
        requested = await self.request_model_list(session)
        if not requested:
            return fail_result(f"{self.kind} does not support /models discovery")
        pane = await tmux.capture_pane(session, lines=200)
        models = parse_harness_model_list(pane)
        if not models:
            return fail_result(f"{self.kind} /models did not expose any model choices")
        return ok_result(models)

    @abstractmethod
    def extract_last_message(self, pane_text: str) -> str | None: ...

    def has_transcript_parser(self) -> bool:
        return bool(self.transcript_prompt_markers)

    def parse_transcript(self, pane_text: str) -> list[tuple[str, str]]:
        """Best-effort ``(role, text)`` turns visible in the session pane.

        Returns the *full* visible transcript on every call — never deltas;
        :func:`murder.conversation.merge_transcript` reconciles successive
        parses into the persisted log. ``role`` is ``"user"`` or
        ``"assistant"``. The default uses the prompt-marker heuristic keyed by
        :attr:`transcript_prompt_markers` / :attr:`transcript_drop_substrings`;
        a harness with cleaner UI structure should override this with something
        tighter (and fixture-test it against a real capture).
        """
        return parse_prompt_marker_transcript(
            pane_text,
            prompt_markers=self.transcript_prompt_markers,
            drop_substrings=self.transcript_drop_substrings,
        )

    def detect_ask(self, pane_text: str) -> str | None:
        m = ASK_RE.search(strip_ui_chrome(pane_text))
        return m.group("body").strip() if m else None

    def detect_asks(self, pane_text: str) -> list[str]:
        clean = strip_ui_chrome(pane_text)
        return [m.group("body").strip() for m in ASK_RE.finditer(clean)]

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
        return bool(DONE_RE.search(strip_ui_chrome(pane_text)))

    @abstractmethod
    def format_nudge(self, msg: str) -> str: ...

    async def interrupt(self, session: str) -> None:
        await tmux.interrupt(session)
