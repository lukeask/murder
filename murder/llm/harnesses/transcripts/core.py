"""Transcript accumulator and parse pipeline core.

Harness-agnostic; all harness-specific logic is in the grammar plugins.
This module imports NO harness adapter, breaking the parser↔adapter cycle.

Pipeline (one shape for all harnesses):

    feed(frame) -> _PaneScrollback splices successive fixed-height captures into
    one monotonic list of logical lines on stable absolute coordinates -> the
    per-harness grammar parses those lines top-to-bottom into span-annotated
    segments, in pane order -> the accumulator reconciles the fresh parse against
    the committed history and freezes everything above the live window.

Substrate invariants (Phase A):

  - **Monotonic coordinates.** ``_PaneScrollback.lines`` only grows; an index
    into it, once assigned, denotes the same logical scrollback line for the
    life of an epoch. The splice overwrites in place inside the visible window
    and appends below it — it never re-indexes existing lines.
  - **Exact-overlap alignment.** Successive captures are aligned by the smallest
    shift whose overlap *above the live window* matches byte-for-byte. The
    weighted-score heuristic the pipeline used to re-derive stability every tick
    is gone; alignment is deterministic.
  - **Epochs.** A capture that cannot be aligned (screen clear, /clear,
    compaction redraw, harness restart) starts a new epoch: committed history is
    kept, coordinates restart below it, parsing continues by appending.
  - **Positional commitment.** Segments whose spans lie entirely above the live
    window are committed and carried verbatim. A re-parse of frozen lines that
    disagrees with a committed segment is an invariant breach (a grammar that is
    non-deterministic over stable text) — the committed segment wins and the
    breach is counted, not silently swallowed.
"""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Iterable
from typing import Any

from murder.observability.advanced_log import ParserRecord, current_advanced_log

from murder.llm.harnesses.parsing import strip_ansi
from murder.llm.harnesses.transcripts._shared import (
    _dedupe_adjacent,
    _segment_key,
    dedupe_adjacent_spanned,
)
from murder.llm.harnesses.transcripts.registry import get_grammar, supports_harness
from murder.llm.harnesses.transcripts.segments import Segment, SpannedSegment

# Conservative default live window when the caller does not know the pane height.
# Correctness only requires ``live_window >= actual pane height``; a larger value
# is safe (it just freezes committed history a little later). The producer may
# pass the real ``pane_height`` to tighten it.
_DEFAULT_LIVE_WINDOW = 200


# Re-export public symbols for callers importing from this module directly.
__all__ = [
    "TranscriptAccumulator",
    "parse_frames",
    "_dedupe_adjacent",
    "_segment_key",
    "_strip_leading_system_prompt",
    "_PaneScrollback",
    "BreachCounters",
]


class BreachCounters:
    """C2 invariant-breach hooks.

    A lightweight, zero-cost-by-default tally of the events that used to be
    swallowed silently. They are surfaced on the accumulator (``acc.breaches``)
    so tests can assert them and a debug path can log them; they never alter
    parsed output.

    - ``committed_mutations``: a fresh parse of *frozen* lines produced a segment
      that disagrees with the already-committed one. Means a grammar is
      non-deterministic over text the terminal guarantees is stable.
    - ``splice_resets``: a capture could not be aligned and started a new epoch.
    - ``chrome_in_segments``: reserved for a chrome-leak audit; tests scan final
      segment text for known chrome substrings (see the conformance tests) and a
      debug path may bump this. It stays zero on the deterministic feed path.
    - ``dropped_completions``: a grammar saw a completion marker (``✻ … for …`` /
      ``─ Worked for … ─``) with no assistant block to attach it to, so the
      elapsed/final tag was dropped. The grammar tallies it during its pure parse
      (``toolkit.record_dropped_completion``) and the accumulator drains it here.
    """

    __slots__ = (
        "committed_mutations",
        "splice_resets",
        "chrome_in_segments",
        "dropped_completions",
    )

    def __init__(self) -> None:
        self.committed_mutations = 0
        self.splice_resets = 0
        self.chrome_in_segments = 0
        self.dropped_completions = 0


def _has_content(lines: list[str]) -> bool:
    """True if any line carries non-blank text."""
    return any(line.strip() for line in lines)


def _shares_nonblank_line(a: list[str], b: list[str]) -> bool:
    """True if ``a`` and ``b`` share any non-blank line (position-independent).

    Distinguishes a redraw that keeps anchor lines (prompt echo, status bar) from
    a true screen replacement where nothing carries over. Used only to gate the
    short-scrollback reset, so the O(n) set build runs only on a zero single-shift
    alignment, not every tick.
    """
    a_lines = {line for line in a if line.strip()}
    return any(line in a_lines for line in b if line.strip())


class _PaneScrollback:
    """Reconcile successive fixed-height pane captures into logical scrollback.

    Coordinates are absolute and monotonic within an epoch (see module docstring).
    ``start`` is the absolute index of the visible pane's top line; ``epoch`` is
    bumped on every alignment failure so callers can tell history apart.
    """

    def __init__(self, *, live_window: int = _DEFAULT_LIVE_WINDOW) -> None:
        self.lines: list[str] = []
        self._previous: list[str] = []
        self.start = 0
        self.epoch = 0
        self.live_window = live_window
        self.last_reset = False

    def _align(self, new: list[str]) -> int | None:
        """Shift ``d`` of ``new`` over ``_previous`` whose overlap above the
        previous frame's live window matches exactly, or None if none does.

        Mismatches are tolerated only inside the trailing ``live_window`` lines of
        the previous frame — the cells tmux may have redrawn. Above that the
        overlap must be byte-identical, which is what makes the alignment exact
        rather than the old best-effort weighted score.

        Among valid shifts we pick the one with the most matched non-blank lines
        (the genuine scroll), tie-broken to the smallest ``d``. Picking the
        smallest ``d`` outright would mis-read every scroll as ``d == 0`` (no
        movement) whenever the live window covers the whole pane.
        """
        prev = self._previous
        frozen_cut = len(prev) - self.live_window
        best_d: int | None = None
        best_matched = -1
        for d in range(len(prev) + 1):
            ok = True
            matched = 0
            for j, line in enumerate(new):
                i = j + d
                if i >= len(prev):
                    break
                if line == prev[i]:
                    if line.strip():
                        matched += 1
                elif i < frozen_cut:
                    ok = False
                    break
            if ok and matched > best_matched:
                best_matched = matched
                best_d = d
        # A zero single-shift score (``best_matched <= 0``) with frozen content
        # above the window is a replaced screen — reset (the original guard).
        if best_matched <= 0 and frozen_cut > 0:
            return None
        # When the whole previous frame fits inside the live window
        # (``frozen_cut <= 0``, e.g. an early ``/clear``) the original guard never
        # fired, so a wholly unrelated frame aligned at d==0 and silently
        # overwrote the existing lines in place with no epoch bump. Reset that case
        # too — but only on a *true* screen replacement: no non-blank line is
        # shared at any position. A redraw that streams new prose while keeping a
        # few anchor lines (prompt echo, status) still shares content and must
        # overwrite in place, not churn an epoch. The first feed into an empty
        # scrollback and all-blank captures share nothing yet must not reset, so we
        # also require both sides to carry non-blank content.
        if (
            best_matched <= 0
            and _has_content(prev)
            and _has_content(new)
            and not _shares_nonblank_line(prev, new)
        ):
            return None
        return best_d

    def feed(self, pane_text: str) -> None:
        new = strip_ansi(pane_text).splitlines()
        self.last_reset = False
        if not self._previous:
            self.lines = list(new)
            self._previous = new
            self.start = 0
            return

        shift = self._align(new)
        if shift is None:
            # Reset: keep history, restart coordinates below it as a new epoch.
            self.start = len(self.lines)
            self.lines.extend(new)
            self._previous = new
            self.epoch += 1
            self.last_reset = True
            return

        self.start += shift
        end = self.start + len(new)
        if end > len(self.lines):
            self.lines.extend([""] * (end - len(self.lines)))
        self.lines[self.start : end] = new
        self._previous = new

    @property
    def live_top(self) -> int:
        """Absolute index of the top of the live window: segments ending at or
        below it are frozen (committed); segments overlapping it are provisional."""
        return max(0, len(self.lines) - self.live_window)


def _reconcile(
    committed: list[SpannedSegment], parsed: list[SpannedSegment]
) -> list[SpannedSegment]:
    """Positional commitment, replacing the old LCS-on-text-prefix merge.

    Spans travel with every segment, so a re-parse and the committed history
    share one coordinate system. We carry committed segments forward and fold the
    fresh parse in, reconciled by an LCS over segment identity. Identity is still
    a content key (``_segment_key``) rather than pure span position: codex
    redraws a block (the plan checklist, a streaming tool) *in place* and only
    later scrolls it, so two segments legitimately occupy the same coordinates at
    different times — position alone cannot tell them apart, content can. See the
    Phase A report for why the goldens force this.
    """
    if not committed:
        return [_clone(s) for s in parsed]
    if not parsed:
        return committed

    keys_c = [_segment_key(s.segment) for s in committed]
    keys_p = [_segment_key(s.segment) for s in parsed]
    n, m = len(committed), len(parsed)
    lcs = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if keys_c[i] == keys_p[j]:
                lcs[i][j] = 1 + lcs[i + 1][j + 1]
            else:
                lcs[i][j] = max(lcs[i + 1][j], lcs[i][j + 1])

    merged: list[SpannedSegment] = []
    i = j = 0
    while i < n and j < m:
        if keys_c[i] == keys_p[j]:
            merged.append(_clone(parsed[j]))
            i += 1
            j += 1
        elif lcs[i + 1][j] >= lcs[i][j + 1]:
            merged.append(committed[i])
            i += 1
        else:
            merged.append(_clone(parsed[j]))
            j += 1
    merged.extend(committed[i:])
    merged.extend(_clone(s) for s in parsed[j:])
    return merged


def _clone(spanned: SpannedSegment) -> SpannedSegment:
    return SpannedSegment(
        copy.deepcopy(spanned.segment), spanned.start, spanned.end, spanned.epoch
    )


def _count_committed_mutations(
    committed: list[SpannedSegment], parsed: list[SpannedSegment], live_top: int
) -> int:
    """C2(a): how many frozen committed segments the fresh parse disagrees with.

    A committed segment lies above the live window; a re-parse of those same
    stable lines should reproduce it. If a parsed segment shares its span but not
    its payload, a grammar is non-deterministic over frozen text — exactly the
    bug class the old pipeline hid. We only count; ``_reconcile`` keeps the
    committed value either way.
    """
    parsed_by_start = {s.start: s for s in parsed if not s.pinned}
    breaches = 0
    for c in committed:
        if c.pinned or c.end > live_top:
            continue
        fresh = parsed_by_start.get(c.start)
        if fresh is not None and fresh.segment != c.segment:
            breaches += 1
    return breaches


def _resolve_choice_prompt(
    spanned: list[SpannedSegment], prompt: Any, segment_key_fn: Any, choice_segment_fn: Any
) -> list[SpannedSegment]:
    """Mark the live choice prompt answered once the harness reports a selection.

    Resolution prefers the pinned span the prompt was injected at (a stable
    coordinate handle) and only falls back to a content-key match — so a second
    identical prompt elsewhere is never resolved by accident.
    """
    target_key = segment_key_fn(choice_segment_fn(prompt))
    for index in range(len(spanned) - 1, -1, -1):
        segment = spanned[index].segment
        if segment["type"] != "choice_prompt" or segment.get("answered"):
            continue
        if not spanned[index].pinned and segment_key_fn(segment) != target_key:
            continue
        resolved = copy.deepcopy(segment)
        resolved["answered"] = True
        # Multi-select resolves to the set of checked numbers at the last live
        # view; single-select to the cursor's option number.
        if getattr(prompt, "multi_select", False):
            resolved["chosen"] = list(prompt.checked_numbers)
        else:
            resolved["chosen"] = prompt.selected_option.number
        spanned[index] = SpannedSegment(
            resolved, spanned[index].start, spanned[index].end, spanned[index].epoch
        )
        break
    # The choice dialog question is stored in choice_prompt.question; drop any
    # assistant segment with the identical text so it does not appear twice.
    question = (prompt.question or "").strip()
    if question:
        spanned = [
            s
            for s in spanned
            if not (
                s.segment.get("type") == "assistant"
                and s.segment.get("text", "").strip() == question
            )
        ]
    return spanned


def _strip_leading_system_prompt(
    blocks: list[list[str]], system_prompt: str | None
) -> list[list[str]]:
    """Re-export of the pure helper in _shared for backward compatibility."""
    from murder.llm.harnesses.transcripts._shared import strip_leading_system_prompt  # noqa: PLC0415

    return strip_leading_system_prompt(blocks, system_prompt)


class TranscriptAccumulator:
    """Append pane captures and expose the accumulated typed transcript."""

    def __init__(
        self,
        harness: str,
        *,
        system_prompt: str | None = None,
        pane_height: int | None = None,
    ) -> None:
        self.harness = harness
        self.system_prompt = system_prompt
        # Ground-truth user turns (recorded at the send boundary), used by
        # markerless grammars to recognise echoed user content. Updated by the
        # producer before each feed; empty is fine.
        self.user_texts: list[str] = []
        live_window = pane_height if pane_height is not None else _DEFAULT_LIVE_WINDOW
        self._scrollback = _PaneScrollback(live_window=live_window)
        self._state = "working"
        self._committed: list[SpannedSegment] = []
        self._active_choice_prompt: Any = None
        self.breaches = BreachCounters()

    def feed(self, frame: str) -> None:
        grammar = get_grammar(self.harness) if supports_harness(self.harness) else None

        # Per-harness frame preprocessing (e.g. cursor tags colour-coded user
        # lines) runs on the raw capture before scrollback reconciliation.
        if grammar is not None and hasattr(grammar, "preprocess_frame"):
            frame = grammar.preprocess_frame(frame)
        self._scrollback.feed(frame)
        if self._scrollback.last_reset:
            self.breaches.splice_resets += 1

        live_choice_prompt = None
        if grammar is not None:
            live_choice_prompt = grammar.detect_live_choice_prompt(frame)

        if live_choice_prompt is not None:
            self._state = "awaiting_approval"
        elif grammar is not None and grammar.is_idle(frame):
            self._state = "awaiting_input"
        else:
            self._state = "working"

        from murder.llm.harnesses.transcripts.toolkit import (  # noqa: PLC0415
            drain_dropped_completions,
        )
        # The dropped-completion tally is a process-wide singleton in toolkit.
        # feed() is synchronous, so single-threaded asyncio cannot interleave two
        # feeds mid-parse; the only way a stale count reaches us is an exception
        # raised inside a *previous* parse_spanned after it recorded a drop. Clear
        # the tally immediately before this parse so we only ever attribute the
        # drops this call produced, then drain them right after.
        drain_dropped_completions()
        if grammar is not None:
            parsed = grammar.parse_spanned(
                self._scrollback.lines, self.system_prompt, self.user_texts
            )
        else:
            parsed = []
        # Drain any completion markers the grammar saw but could not attribute to
        # an assistant block (C2: a dropped signal, counted not swallowed).
        self.breaches.dropped_completions += drain_dropped_completions()

        if live_choice_prompt is not None:
            from murder.llm.harnesses.transcripts.grammar.claude_code import (  # noqa: PLC0415
                choice_prompt_segment,
            )
            # The choice question text was emitted by the assistant and then the
            # dialog rendered. parse_spanned may capture it as an assistant
            # segment; remove it so it does not duplicate choice_prompt.question.
            question = (live_choice_prompt.question or "").strip()
            if question:
                parsed = [
                    s
                    for s in parsed
                    if not (
                        s.segment.get("type") == "assistant"
                        and s.segment.get("text", "").strip() == question
                    )
                ]
            # The prompt is injected live, not read from scrollback lines: pin it
            # at the bottom of the current pane so commitment treats it as live.
            pin = len(self._scrollback.lines)
            parsed.append(SpannedSegment(choice_prompt_segment(live_choice_prompt), pin, pin))

        # C2(a): a frozen committed segment a re-parse disagrees with is a breach.
        self.breaches.committed_mutations += _count_committed_mutations(
            self._committed, parsed, self._scrollback.live_top
        )

        self._committed = dedupe_adjacent_spanned(_reconcile(self._committed, parsed))

        if live_choice_prompt is not None:
            self._active_choice_prompt = live_choice_prompt
        elif self._active_choice_prompt is not None:
            from murder.llm.harnesses.transcripts.grammar.claude_code import (  # noqa: PLC0415
                choice_prompt_segment,
            )
            self._committed = _resolve_choice_prompt(
                self._committed,
                self._active_choice_prompt,
                _segment_key,
                choice_prompt_segment,
            )
            self._active_choice_prompt = None

        # Boundary #5a: record the incremental parse for the flight recorder.
        # Pass only the freshly-parsed segments (the per-frame delta), not the
        # whole accumulated transcript, plus a content hash so the ChangeGate
        # writes one row only when this frame's parse changed since the last.
        parsed_segments = [s.segment for s in parsed]
        dedup_basis = repr(parsed_segments)
        current_advanced_log().record_parser(
            ParserRecord(
                session=None,
                parsed=parsed_segments,
                live_state=self._state,
                dedup_hash=hashlib.sha1(dedup_basis.encode("utf-8")).hexdigest(),
            )
        )

    def to_dict(self) -> dict[str, Any]:
        segments: list[Segment] = [copy.deepcopy(s.segment) for s in self._committed]
        if self._state == "awaiting_input" and supports_harness(self.harness):
            grammar = get_grammar(self.harness)
            grammar.close_last_turn(segments)
        return {
            "harness": self.harness,
            "state": self._state,
            "condensed": None,
            "segments": segments,
        }


def parse_frames(
    harness: str,
    frames: Iterable[str],
    *,
    system_prompt: str | None = None,
    user_texts: list[str] | None = None,
    pane_height: int | None = None,
) -> dict[str, Any]:
    acc = TranscriptAccumulator(harness, system_prompt=system_prompt, pane_height=pane_height)
    if user_texts is not None:
        acc.user_texts = user_texts
    for frame in frames:
        acc.feed(frame)
    return acc.to_dict()
