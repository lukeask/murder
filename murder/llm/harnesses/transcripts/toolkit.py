"""Shared grammar toolkit: the mechanism every harness grammar repeats.

The five harness grammars all do the same handful of things — recognise a live
input box wedged between two horizontal rules, decide whether a line is UI chrome
to skip, attribute a completion marker to the assistant turn it closes, and split
a markerless pane into chrome-delimited blocks. Before this module each grammar
re-implemented those by hand (an identical live-prompt copy-pair, five monolithic
or-cascade chrome predicates, a duplicated reversed-scan completion attribution,
two near-identical block loops).

This module holds that mechanism once as small composable pieces; the grammars
keep their own marker tables and harness quirks but call in here for the shared
parts. The toolkit is data + pure functions, not a DSL: anything that does not
fit cleanly stays a local hook in the grammar (Ousterhout — deep module, simple
interface; keep the grammar readable rather than contorting it into config).

Imports nothing from murder beyond the segment types and `_shared`, preserving
the no-cycle rule (core/grammars never import each other).
"""

from __future__ import annotations

import re
from collections.abc import Callable

from murder.llm.harnesses.transcripts._shared import _RULE_RE
from murder.llm.harnesses.transcripts.segments import SpannedSegment

# A chrome rule answers "is this raw line UI noise?" for one harness. Rules
# compose by OR: a line is chrome if any rule fires. Each takes the raw line
# (not stripped) so a rule can key off leading whitespace where that matters.
ChromeRule = Callable[[str], bool]


def is_rule_sandwiched(lines: list[str], index: int) -> bool:
    """True when ``lines[index]`` sits between two horizontal rules.

    Both the Claude Code ``❯`` box and the antigravity ``>`` box render as a
    prompt glyph wedged between two ``────`` rules; that sandwich is what tells a
    *live* input box apart from a submitted user turn (which has no trailing
    rule). Blank lines on either side are skipped before the rule check.
    """
    before = index - 1
    while before >= 0 and not lines[before].strip():
        before -= 1
    after = index + 1
    while after < len(lines) and not lines[after].strip():
        after += 1
    return (
        before >= 0
        and after < len(lines)
        and bool(_RULE_RE.match(lines[before]))
        and bool(_RULE_RE.match(lines[after]))
    )


# ---- composable chrome rule builders --------------------------------------- #
#
# These return one-line predicates so a grammar can declare its chrome set as a
# tuple of rules read top-to-bottom, instead of a hand-ordered boolean cascade.


def blank_rule() -> ChromeRule:
    """Blank / whitespace-only line."""
    return lambda line: not line.strip()


def horizontal_rule() -> ChromeRule:
    """A ``────`` style horizontal rule (the shared ``_RULE_RE``)."""
    return lambda line: bool(_RULE_RE.match(line))


def regex_match_rule(pattern: re.Pattern[str]) -> ChromeRule:
    """Line matches ``pattern`` at its start (``re.match`` on the raw line)."""
    return lambda line: bool(pattern.match(line))


def regex_search_rule(pattern: re.Pattern[str]) -> ChromeRule:
    """``pattern`` appears anywhere in the raw line (``re.search``)."""
    return lambda line: bool(pattern.search(line))


def substring_rule(*needles: str) -> ChromeRule:
    """Any needle appears in the raw line."""
    return lambda line: any(needle in line for needle in needles)


def stripped_substring_rule(*needles: str) -> ChromeRule:
    """Any needle appears in the line after stripping surrounding whitespace."""
    return lambda line: any(needle in line.strip() for needle in needles)


def stripped_startswith_rule(*prefixes: str) -> ChromeRule:
    """The stripped line starts with any of ``prefixes``."""
    return lambda line: line.strip().startswith(prefixes)


def chrome_matcher(*rules: ChromeRule) -> ChromeRule:
    """Fold a tuple of chrome rules into one OR predicate.

    The shared base set (blank + horizontal rule) is the common head of every
    harness's cascade; pass it first, then the per-harness extras. Order is
    irrelevant to correctness (pure OR) but reads as "base, then this harness's
    own noise".
    """
    return lambda line: any(rule(line) for rule in rules)


# Base chrome shared by every harness: blank lines and horizontal rules.
BASE_CHROME_RULES: tuple[ChromeRule, ...] = (blank_rule(), horizontal_rule())


def attribute_completion(
    spanned: list[SpannedSegment],
    elapsed: str | None,
    end: int,
    *,
    on_drop: Callable[[], None] | None = None,
) -> bool:
    """Retag the most recent assistant segment as the turn's final answer.

    Claude Code (``✻ … for 3m 59s``) and Codex (``─ Worked for … ─``) both close
    a turn with a completion marker that belongs to the last assistant block
    above it. Both grammars scanned ``spanned`` in reverse for that block, set its
    phase to ``final``, stamped the elapsed duration, and widened its span to
    cover the marker. That scan lives here once.

    Returns whether an assistant block was found. If none was (the completion
    marker scrolled in without its assistant text, or a grammar mis-segmented),
    the completion is dropped — ``on_drop`` is invoked so the caller can count it
    via the breach/chrome tally (see ``record_dropped_completion``) rather than
    discarding it silently.
    """
    for item in reversed(spanned):
        if item.segment["type"] == "assistant":
            item.segment["phase"] = "final"
            item.segment["elapsed"] = elapsed
            item.end = max(item.end, end)
            return True
    if on_drop is not None:
        on_drop()
    return False


class _DroppedCompletionTally:
    """A process-wide count of completion markers no assistant block could claim.

    A dropped completion is the same class of swallowed signal the C2 breach
    counters surface, but it is detected inside a *pure* grammar parse that has no
    handle on the accumulator. The grammar bumps this tally via
    ``record_dropped_completion``; the accumulator drains it into ``acc.breaches``
    after each parse (see ``BreachCounters.dropped_completions``). A module
    singleton is acceptable here — the parse pipeline runs single-threaded per
    tick and the count is observability only; it never alters parsed output. A
    held instance (rather than a bare module global) keeps mutation out of a
    ``global`` statement.
    """

    __slots__ = ("count",)

    def __init__(self) -> None:
        self.count = 0


_DROPPED = _DroppedCompletionTally()


def record_dropped_completion() -> None:
    """Default ``on_drop`` hook: tally a completion marker with no assistant."""
    _DROPPED.count += 1


def drain_dropped_completions() -> int:
    """Return the dropped-completion count since the last drain, and reset it."""
    count = _DROPPED.count
    _DROPPED.count = 0
    return count


def collect_chrome_delimited_blocks(
    lines: list[str], is_chrome: ChromeRule
) -> list[tuple[list[str], int, int]]:
    """Split lines into chrome-delimited blocks of non-chrome content.

    The markerless grammars (cursor, pi) have no role glyphs, so they segment the
    pane into runs of consecutive non-chrome lines separated by chrome. Each block
    carries its absolute ``[start, end)`` span so commitment can place it. A chrome
    line both ends the current block and is itself dropped.
    """
    blocks: list[tuple[list[str], int, int]] = []
    current: list[str] = []
    block_start = 0
    for index, line in enumerate(lines):
        if is_chrome(line):
            if current:
                blocks.append((current, block_start, index))
                current = []
        else:
            if not current:
                block_start = index
            current.append(line)
    if current:
        blocks.append((current, block_start, len(lines)))
    return blocks
