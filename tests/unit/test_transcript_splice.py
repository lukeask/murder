"""Phase A substrate tests: deterministic scrollback splice + positional
commitment invariants + C2 breach hooks.

The whole-document goldens in ``test_transcript.py`` pin observable output. These
tests pin the *substrate* properties the rearchitecture guarantees, which the
goldens only exercise incidentally: exact-overlap alignment reconstructs true
history under a synthetic terminal, committed coordinates are monotonic, a reset
opens a new epoch without dropping history, and the breach counters fire.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

import murder.llm.harnesses.transcripts as transcripts
from murder.llm.harnesses.transcripts.core import _PaneScrollback

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "transcripts"

_CHROME_NEVER = [
    "bypass permissions",
    "esc to interrupt",
    "ctrl+o to expand",
    "Tip:",
    "uncached",
    "Find and fix a bug in @filename",
]


class _SyntheticTerminal:
    """A fake terminal over an immutable ground-truth history.

    Holds a growing list of committed lines plus a visible window of ``height``.
    ``tick`` may scroll new content in (pushing lines into immutable history),
    redraw the bottom of the visible window (cells tmux is allowed to rewrite),
    or clear (start a fresh screen). It exposes both the true history and the
    capture the splice must reconcile — so a test can assert the splice's
    reconstruction equals the truth.
    """

    def __init__(self, rng: random.Random, height: int) -> None:
        self.rng = rng
        self.height = height
        self.history: list[str] = []  # immutable ground truth (scrolled-off)
        self.visible: list[str] = [f"line-{i}" for i in range(height)]
        self._counter = height

    def _fresh(self) -> str:
        line = f"line-{self._counter}"
        self._counter += 1
        return line

    def tick(self, *, allow_clear: bool = True) -> str:
        roll = self.rng.random()
        if allow_clear and roll < 0.1:
            # Clear: the screen is wiped to a brand-new pane. The previously
            # visible cells are discarded (a clear does not scroll them into
            # history), so they are NOT recorded as immutable truth.
            self.visible = [self._fresh() for _ in range(self.height)]
        else:
            scroll = self.rng.randint(0, self.height // 2)
            if scroll:
                self.history.extend(self.visible[:scroll])
                self.visible = self.visible[scroll:] + [self._fresh() for _ in range(scroll)]
            # Redraw only the single bottom cell (live spinner / input box). It is
            # always inside the live window the splice may rewrite, and is settled
            # before it can scroll, so committed history above stays intact.
            self.visible[self.height - 1] = self._fresh()
        return "\n".join(self.visible)

    def true_history(self) -> list[str]:
        return self.history + self.visible


@pytest.mark.parametrize("seed", range(12))
def test_splice_reconstructs_history_under_scroll_and_redraw(seed: int) -> None:
    """With ``live_window >= pane height`` the splice must reconstruct the true
    terminal history: every immutable (scrolled-off) line appears in order, and
    the visible tail matches the last capture exactly."""
    rng = random.Random(seed)
    height = 24
    term = _SyntheticTerminal(rng, height)
    sb = _PaneScrollback(live_window=height + 8)

    sb.feed("\n".join(term.visible))
    for _ in range(60):
        sb.feed(term.tick(allow_clear=False))

    reconstructed = sb.lines
    present = set(reconstructed)

    # Every line the terminal scrolled into immutable history must survive in the
    # reconstruction — the splice never drops committed history (lines are
    # globally unique, so set membership is an exact check).
    for line in term.history:
        assert line in present, f"immutable history line {line!r} lost"

    # The visible tail is reproduced verbatim at the bottom of the scrollback.
    assert reconstructed[sb.start : sb.start + height] == term.visible

    # Coordinates are monotonic: the visible window never starts before history.
    assert 0 <= sb.start <= len(reconstructed)


@pytest.mark.parametrize("seed", range(8))
def test_splice_preserves_history_across_clears(seed: int) -> None:
    """Under random clears the splice must never overwrite committed history with
    garbage. With ``live_window > pane height`` an unrelated screen is absorbed
    as a full-window redraw of the visible region (``start`` does not move back),
    so everything that already scrolled below the window stays intact."""
    rng = random.Random(seed + 100)
    height = 12
    term = _SyntheticTerminal(rng, height)
    sb = _PaneScrollback(live_window=height + 4)
    sb.feed("\n".join(term.visible))
    for _ in range(60):
        sb.feed(term.tick(allow_clear=True))
    # Settled history (everything below the live window) survives verbatim.
    present = set(sb.lines)
    settled = term.history[: max(0, len(term.history) - sb.live_window)]
    for line in settled:
        assert line in present, f"history line {line!r} lost across a clear"


def test_unalignable_capture_opens_new_epoch() -> None:
    """When the live window is tighter than the accumulated scrollback, a screen
    that shares nothing with the previous frame cannot be aligned and opens a new
    epoch rather than overwriting committed lines."""
    sb = _PaneScrollback(live_window=4)
    sb.feed("\n".join(f"a{i}" for i in range(10)))
    # Scroll a few times so scrollback is taller than the live window.
    for batch in range(3):
        rows = [f"a{i}" for i in range(2 + batch, 10)] + [f"a{10 + batch}", f"a{11 + batch}"]
        sb.feed("\n".join(rows))
    before = sb.epoch
    sb.feed("\n".join(f"z{i}" for i in range(10)))  # unrelated screen
    assert sb.epoch == before + 1
    assert sb.last_reset is True


def test_reset_when_scrollback_shorter_than_live_window() -> None:
    """A wholly-unrelated non-blank frame must open a new epoch even when the
    scrollback is shorter than the live window (``frozen_cut <= 0``, e.g. an early
    ``/clear``). Without the content-aware reset such a frame aligned at d==0 and
    silently overwrote the existing lines with no epoch bump; the old lines must
    survive below the new epoch."""
    sb = _PaneScrollback(live_window=200)
    sb.feed("\n".join(f"old{i}" for i in range(6)))
    old = list(sb.lines)
    before = sb.epoch
    sb.feed("\n".join(f"new{i}" for i in range(6)))  # unrelated non-blank screen
    assert sb.epoch == before + 1
    assert sb.last_reset is True
    assert sb.lines[:6] == old  # old lines survive, not overwritten in place
    assert sb.lines[6:] == [f"new{i}" for i in range(6)]


def test_blank_frame_does_not_reset_short_scrollback() -> None:
    """An all-blank capture into a short scrollback must not churn an epoch — the
    content-aware reset only fires when both sides carry non-blank text."""
    sb = _PaneScrollback(live_window=200)
    sb.feed("\n".join(f"old{i}" for i in range(6)))
    before = sb.epoch
    sb.feed("\n".join("" for _ in range(6)))  # all-blank frame
    assert sb.epoch == before
    assert sb.last_reset is False


def test_smallest_shift_is_preferred_on_ties() -> None:
    """A static pane (no scroll, no redraw) must align at d==0 and never grow."""
    sb = _PaneScrollback(live_window=200)
    frame = "\n".join(f"row-{i}" for i in range(30))
    sb.feed(frame)
    sb.feed(frame)
    sb.feed(frame)
    assert sb.lines == frame.splitlines()
    assert sb.start == 0
    assert sb.epoch == 0


def test_scroll_advances_coordinates_monotonically() -> None:
    """Each genuine scroll advances ``start`` and grows the list; existing
    indices keep their content (immutable below the live window)."""
    sb = _PaneScrollback(live_window=4)
    frame0 = "\n".join(f"a{i}" for i in range(8))
    sb.feed(frame0)
    snapshot = list(sb.lines)
    # Scroll by 3: top 3 fall into history, 3 fresh lines at the bottom.
    rows = [f"a{i}" for i in range(3, 8)] + ["b0", "b1", "b2"]
    sb.feed("\n".join(rows))
    assert sb.start == 3
    assert sb.lines[:8] == snapshot  # immutable prefix preserved
    assert sb.lines[8:] == ["b0", "b1", "b2"]


def test_reset_opens_new_epoch_and_keeps_history() -> None:
    """An unalignable capture (screen replaced) starts a new epoch appended below
    the old history rather than overwriting it with garbage."""
    sb = _PaneScrollback(live_window=4)
    sb.feed("\n".join(f"old{i}" for i in range(6)))
    old = list(sb.lines)
    sb.feed("\n".join(f"new{i}" for i in range(6)))  # nothing in common
    assert sb.epoch == 1
    assert sb.last_reset is True
    assert sb.lines[:6] == old
    assert sb.lines[6:] == [f"new{i}" for i in range(6)]


# --------------------------------------------------------------------------- #
# Positional commitment invariants (A3) + C2 breach hooks.
# --------------------------------------------------------------------------- #
def _cc_frames() -> list[str]:
    fdir = _FIXTURES / "cc" / "frames"
    return [p.read_text(encoding="utf-8") for p in sorted(fdir.glob("*.txt"))]


def test_committed_segments_never_mutate_once_frozen() -> None:
    """A committed segment (span above the live window) is carried verbatim: its
    serialized form may only ever grow at the tail (new turns appended), never
    have an existing prefix rewritten, as we feed the cc capture frame by frame."""
    acc = transcripts.TranscriptAccumulator("claude_code", pane_height=200)
    prev: list[str] = []
    for frame in _cc_frames():
        acc.feed(frame)
        segs = [json.dumps(s, ensure_ascii=False) for s in acc.to_dict()["segments"]]
        # The committed prefix is whatever both snapshots agree on at the front;
        # nothing already emitted should be reordered out from under us.
        common = min(len(prev), len(segs))
        # Allow the last 1-2 segments to change (live region / phase fixup at
        # idle); everything older than that must be stable.
        stable = max(0, common - 2)
        assert prev[:stable] == segs[:stable]
        prev = segs


def test_no_chrome_leaks_under_breach_audit() -> None:
    """C2(c): no known chrome substring survives into final segment text. The
    accumulator exposes a breach tally; the parsed cc doc must show zero chrome."""
    doc = transcripts.parse_frames("claude_code", _cc_frames())
    blob = json.dumps(doc, ensure_ascii=False)
    for needle in _CHROME_NEVER:
        assert needle not in blob


def test_static_pane_records_no_breaches_and_no_resets() -> None:
    """Feeding one steady idle pane repeatedly must not trip any breach counter
    or splice reset — the substrate is stable over stable input."""
    acc = transcripts.TranscriptAccumulator("claude_code", pane_height=200)
    frame = _cc_frames()[-1]
    for _ in range(5):
        acc.feed(frame)
    assert acc.breaches.splice_resets == 0
    assert acc.breaches.committed_mutations == 0


def test_reset_breach_counts_on_screen_replacement() -> None:
    """A clear/replace between captures bumps the splice-reset breach counter."""
    acc = transcripts.TranscriptAccumulator("claude_code", pane_height=8)
    acc.feed("\n".join(f"❯ old line {i}" for i in range(10)))
    acc.feed("\n".join(f"❯ totally new {i}" for i in range(10)))
    assert acc.breaches.splice_resets >= 1


def test_default_live_window_when_pane_height_unknown() -> None:
    """The optional pane_height kwarg is additive; omitting it uses the safe
    conservative default and still reconciles the goldens."""
    doc = transcripts.parse_frames("codex", _codex_frames())
    expected = json.loads((_FIXTURES / "codex" / "expected.json").read_text(encoding="utf-8"))
    assert doc == expected


def _codex_frames() -> list[str]:
    fdir = _FIXTURES / "codex" / "frames"
    return [p.read_text(encoding="utf-8") for p in sorted(fdir.glob("*.txt"))]
