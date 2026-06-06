"""Maps harness kind to grammar plugin module.

The grammar module protocol (duck-typed, not a formal Protocol class):
  parse_lines(lines, system_prompt=None) -> list[Segment]
  is_idle(pane_text) -> bool
  detect_live_choice_prompt(frame) -> MultipleChoicePrompt | None
  close_last_turn(segments) -> None  (mutates in place; called at idle)

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
