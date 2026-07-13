"""Harness interface and live tmux session facade."""

from __future__ import annotations

import asyncio
import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar, Literal

from murder.llm.harnesses.capabilities import HarnessCapabilities
from murder.llm.harnesses.models import (
    HarnessModelState,
    HarnessStartSpec,
)
from murder.llm.harnesses.parsing import strip_ui_chrome
from murder.llm.harnesses.results import SimpleResult, fail_result, ok_result
from murder.llm.harnesses.transcripts import SEGMENT_TYPES, parse_frames, supports_harness
from murder.runtime.terminal import tmux

_log = logging.getLogger(__name__)

ASK_RE = re.compile(r">>>\s*ASK:\s*(?P<body>.+?)(?=\n>>>|\Z)", re.DOTALL)
ANSWER_RE = re.compile(
    r">>>\s*ANSWER\[(?P<ticket>[^\]]+)\]:\s*(?P<body>.+?)(?=\n>>>|\Z)",
    re.DOTALL,
)
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

    async def start(self, spec: HarnessStartSpec | None = None) -> SimpleResult[None]:
        start_spec = spec or HarnessStartSpec(
            cwd=self.repo_root,
            startup_model=self.adapter.startup_model,
            startup_effort=self.adapter.startup_effort,
        )
        # Requested model fields remain launch metadata for higher-level
        # verified control.  Legacy startup never turns them into a CLI flag or
        # terminal picker workflow.
        if start_spec.startup_model is not None:
            self.adapter.startup_model = start_spec.startup_model
        if start_spec.startup_effort is not None:
            self.adapter.startup_effort = start_spec.startup_effort
        self.adapter.additional_workspace_dirs = tuple(
            Path(path) for path in start_spec.additional_workspace_dirs
        )
        if start_spec.resume_session_id is not None:
            self.adapter.resume_session_id = start_spec.resume_session_id
        if start_spec.binary is not None:
            self.adapter.binary = start_spec.binary
        await tmux.create_session(
            self.session,
            start_spec.cwd,
            self.adapter.startup_cmd(start_spec.cwd),
        )

        ready = await self._wait_startup_ready(start_spec)
        if not ready.ok:
            return ready
        return ok_result()

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

class HarnessAdapter(ABC):
    kind: ClassVar[str]
    crow_system_prompt: ClassVar[str]
    # Passive configured catalog metadata.  Verified harness control owns every
    # runtime selection effect; these values never drive tmux or startup argv.
    available_startup_models: ClassVar[list[tuple[str, str]]] = []
    supported_efforts: ClassVar[tuple[str, ...]] = ()
    default_effort: ClassVar[str] = "medium"
    usage_collection_mode: ClassVar[UsageCollectionMode] = "none"
    supports_subagents: ClassVar[bool] = False
    cheapest_subagent_model: ClassVar[str | None] = None

    def __init__(
        self,
        startup_model: str | None = None,
        startup_effort: str | None = None,
        binary: str | None = None,
    ) -> None:
        self.startup_model = startup_model
        self.startup_effort = startup_effort
        # Optional CLI binary override (argv[0]). None → adapter's built-in
        # default. Set from the start spec in HarnessSession.start(); read by
        # startup_cmd implementations that support a configurable binary.
        self.binary = binary
        # The murder-owned system prompt injected as this session's first user
        # message. Set by the agent at start() so markerless transcript parsers
        # (cursor, pi) can strip it instead of mislabelling it as chat turns.
        self.system_prompt: str | None = None
        self.additional_workspace_dirs: tuple[Path, ...] = ()
        # CC-only: a prior harness session id to resume on launch. Set from the
        # start spec in HarnessSession.start(); read by startup_cmd. None for a
        # fresh session.
        self.resume_session_id: str | None = None

    @classmethod
    def declared_capabilities(cls) -> HarnessCapabilities:
        """Derive capability flags from adapter class vars (registry source of truth)."""
        return HarnessCapabilities(
            usage_reporting=cls.usage_collection_mode != "none",
            # Runtime model interaction is exclusively owned by verified
            # harness control, never by this legacy adapter facade.
            model_discovery=False,
            model_selection=False,
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

    def parse_active_model_state(self, pane_text: str) -> HarnessModelState | None:
        del pane_text
        return None

    def graceful_exit_command(self) -> str | None:
        """Return the command to send for a graceful exit, or None if unsupported."""
        return None

    def detects_invalid_resume(self, pane_text: str) -> bool:
        """Return True when startup output shows a cached resume id is invalid."""
        del pane_text
        return False

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
