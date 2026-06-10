"""Maps harness kind to grammar plugin module.

The grammar module protocol (duck-typed, not a formal Protocol class). Required
callables, all invoked by ``TranscriptAccumulator.feed`` / ``to_dict``:
  parse_spanned(lines, system_prompt=None, user_texts=None) -> list[SpannedSegment]
      the parse: each segment carries its absolute scrollback span. (Grammars
      also expose a span-stripped ``parse_lines`` projection for callers that
      want bare segments, but the accumulator only calls ``parse_spanned``.)
  is_idle(pane_text) -> bool                  is the pane awaiting input?
  detect_live_choice_prompt(frame) -> MultipleChoicePrompt | None
  close_last_turn(segments) -> None  (mutates in place; called at idle)

Optional hooks / attributes (probed with ``hasattr`` / ``getattr``):
  preprocess_frame(frame) -> str   transform a raw frame before scrollback
  WANTS_ANSI: bool                 capture the pane with SGR escapes (tmux -e)

No harness adapter is imported at module level here, keeping the
transcripts package free of circular imports with adapters.
"""

from __future__ import annotations

from types import ModuleType

import importlib

_HARNESS_GRAMMAR_MODULE: dict[str, str] = {
    "claude_code": "murder.llm.harnesses.transcripts.grammar.claude_code",
    "codex": "murder.llm.harnesses.transcripts.grammar.codex",
    "cursor": "murder.llm.harnesses.transcripts.grammar.cursor",
    "pi": "murder.llm.harnesses.transcripts.grammar.pi",
    "antigravity": "murder.llm.harnesses.transcripts.grammar.antigravity",
}


def supports_harness(harness: str) -> bool:
    return harness in _HARNESS_GRAMMAR_MODULE


def get_grammar(harness: str) -> ModuleType:
    """Return the grammar module for `harness`, or raise KeyError."""
    module_path = _HARNESS_GRAMMAR_MODULE[harness]
    return importlib.import_module(module_path)


def wants_ansi(harness: str) -> bool:
    """True if the harness's grammar needs the pane captured with SGR escapes.

    Markerless harnesses that colour-code user input (cursor) set
    ``WANTS_ANSI = True`` on their grammar module so capture sites preserve the
    escapes its ``preprocess_frame`` reads. Unknown harnesses: ``False``.
    """
    if not supports_harness(harness):
        return False
    return bool(getattr(get_grammar(harness), "WANTS_ANSI", False))
