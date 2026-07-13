"""Murder protocol signals derived from already-persisted transcript evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_ASK = re.compile(r">>>\s*ASK:\s*(?P<body>.+?)(?=\n>>>|\Z)", re.DOTALL)
# A live block may still grow.  An ASK at its end is therefore incomplete
# until the block seals; during streaming only a following marker closes it.
_ASK_BOUNDED = re.compile(r">>>\s*ASK:\s*(?P<body>.+?)(?=\n>>>)", re.DOTALL)
_ANSWER = re.compile(
    r">>>\s*ANSWER\[(?P<ticket>[^\]]+)\]:\s*(?P<body>.+?)(?=\n>>>|\Z)",
    re.DOTALL,
)
_NOTE = re.compile(r">>>\s*NOTE:\s*(?P<body>.+?)\n>>>\s*END\b", re.DOTALL)
_DONE = re.compile(r"(?:^|(?<=\s))>>>\s*DONE[ \t]*(?:\n|\Z)", re.MULTILINE)
_MAX_NOTE_LINES = 20


@dataclass(frozen=True, slots=True)
class VerifiedOrchestrationSignals:
    state: str | None
    asks: tuple[str, ...]
    answers: tuple[tuple[str, str], ...]
    notes: tuple[str, ...]
    done: bool
    assistant_text: str

    @classmethod
    def from_ingested(cls, ingested: Any) -> VerifiedOrchestrationSignals:
        transcript = next(
            (
                item.payload.get("transcript")
                for item in getattr(ingested, "evidence", ())
                if isinstance(item.payload.get("transcript"), dict)
            ),
            {},
        )
        segments = transcript.get("segments", [])
        assistant_parts = [
            segment["text"]
            for segment in segments
            if isinstance(segment, dict)
            and segment.get("type") == "assistant"
            and isinstance(segment.get("text"), str)
        ]
        assistant = "\n".join(assistant_parts)
        asks = tuple(match.group("body").strip() for match in _ASK.finditer(assistant))
        answers = tuple(
            (
                match.group("ticket").strip(),
                (match.group("body").strip().splitlines() or [""])[0].strip(),
            )
            for match in _ANSWER.finditer(assistant)
        )
        notes = tuple(
            note
            for match in _NOTE.finditer(assistant)
            if (
                note := "\n".join(
                    match.group("body").strip().splitlines()[:_MAX_NOTE_LINES]
                ).strip()
            )
        )
        state = transcript.get("state")
        return cls(
            str(state) if isinstance(state, str) else None,
            asks,
            answers,
            notes,
            bool(_DONE.search(assistant)),
            assistant_parts[-1] if assistant_parts else "",
        )

    @classmethod
    def from_conversation_block_change(cls, change: Any) -> VerifiedOrchestrationSignals:
        """Interpret markers introduced by exactly one projected assistant block.

        The block id is the message identity.  For a growing live block we
        compare the former content with the new content and select only markers
        completed by the newly appended suffix.  A seal-only transition permits
        the one trailing ASK whose completion was the end of the message.
        """
        block = getattr(change, "block", None)
        payload = getattr(block, "payload", None)
        if not isinstance(payload, dict) or payload.get("type") != "assistant":
            return cls(None, (), (), (), False, "")
        text = payload.get("text")
        if not isinstance(text, str):
            return cls(None, (), (), (), False, "")

        previous = getattr(change, "previous_payload", None)
        old_text = previous.get("text") if isinstance(previous, dict) else None
        old_len = len(old_text) if isinstance(old_text, str) and text.startswith(old_text) else 0
        grew = old_len < len(text)
        sealed_now = bool(getattr(block, "sealed", False))
        sealed_before = getattr(change, "previous_sealed", None)

        ask_pattern = _ASK if sealed_now else _ASK_BOUNDED
        ask_matches = list(ask_pattern.finditer(text))
        note_matches = list(_NOTE.finditer(text))
        if grew:
            ask_matches = [match for match in ask_matches if match.end() > old_len]
            note_matches = [match for match in note_matches if match.end() > old_len]
        elif sealed_now and sealed_before is False:
            # Streaming already emitted every marker closed by a following
            # sentinel.  Only an ASK whose terminator is message-final is new.
            ask_matches = [match for match in ask_matches if match.end() == len(text)]
            note_matches = []
        elif old_text is not None:
            return cls(None, (), (), (), False, text)

        asks = tuple(match.group("body").strip() for match in ask_matches)
        notes = tuple(
            note
            for match in note_matches
            if (
                note := "\n".join(
                    match.group("body").strip().splitlines()[:_MAX_NOTE_LINES]
                ).strip()
            )
        )
        return cls(None, asks, (), notes, False, text)


__all__ = ["VerifiedOrchestrationSignals"]
