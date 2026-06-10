/**
 * Ledger test — the responsive list-of-entries primitive.
 *
 * Two layers:
 *  - Pure kernels (`columnsForWidth`, `computeWindow`): column collapse + overflow windowing math,
 *    deterministic and terminal-free.
 *  - Rendered behavior (ink-testing-library): full-width highlight, alternating bg, `…` overflow
 *    indicators, header drop when scrolled, highlight suppressed when blurred.
 *
 * Ledger is presentational (props in, no store/bus), so a plain render with a fixed budget suffices.
 */

import { Box, Text } from 'ink';
import { render } from 'ink-testing-library';
import { describe, expect, it } from 'vitest';
import {
  columnsForWidth,
  computeWindow,
  Ledger,
  type LedgerEntryContext,
} from '../../src/components/Ledger.js';

interface DemoRow {
  readonly name: string;
  readonly date: string;
}

function rows(n: number): DemoRow[] {
  return Array.from({ length: n }, (_, i) => ({ name: `row${i}`, date: `d${i}` }));
}

/**
 * A simple 2-column renderer. Emits a `▌` marker when `ctx.selected` so the focus-gated highlight is
 * detectable in the ANSI-stripped test frame (ink-testing-library drops color/inverse/bg). This
 * mirrors how the panels render a cursor marker; the visual full-width bg is a Box prop verified by
 * eye/probe. Shows the date field only when `columns >= 2` (column collapse).
 */
function renderDemo(row: DemoRow, ctx: LedgerEntryContext): React.ReactNode {
  return (
    <Box flexDirection="row">
      <Text>{ctx.selected ? '▌' : ' '}</Text>
      <Text>{row.name}</Text>
      {ctx.columns >= 2 ? <Text>{` ${row.date}`}</Text> : null}
    </Box>
  );
}

describe('columnsForWidth — collapse', () => {
  it('drops to minColumns when width is too narrow', () => {
    expect(columnsForWidth(10, 1, 2)).toBe(1);
  });
  it('rises to maxColumns when width allows', () => {
    expect(columnsForWidth(80, 1, 2)).toBe(2);
  });
  it('clamps within [min, max]', () => {
    expect(columnsForWidth(1000, 1, 3)).toBe(3);
    expect(columnsForWidth(0, 2, 4)).toBe(2);
  });
});

describe('computeWindow — overflow math', () => {
  it('shows everything with no indicators when it all fits', () => {
    expect(computeWindow(3, 0, 1, 10, false)).toEqual({
      start: 0,
      end: 3,
      moreAbove: false,
      moreBelow: false,
    });
  });

  it('reserves a bottom … when rows are below the viewport', () => {
    // 20 rows, 1 line each, height 5, no header, cursor at top.
    const w = computeWindow(20, 0, 1, 5, false);
    expect(w.start).toBe(0);
    expect(w.moreAbove).toBe(false);
    expect(w.moreBelow).toBe(true);
    // One line spent on the bottom … → 4 visible entries.
    expect(w.end).toBe(4);
  });

  it('keeps the cursor visible when scrolled down', () => {
    const w = computeWindow(20, 19, 1, 5, false);
    expect(w.start).toBeLessThanOrEqual(19);
    expect(w.end).toBe(20);
    expect(w.moreAbove).toBe(true);
    expect(w.moreBelow).toBe(false);
    // The cursor (19) is within [start, end).
    expect(19 >= w.start && 19 < w.end).toBe(true);
  });

  it('shows a top … (no bottom) when scrolled to the very bottom', () => {
    const w = computeWindow(20, 19, 1, 5, false);
    expect(w.moreAbove).toBe(true);
    expect(w.moreBelow).toBe(false);
  });

  it('handles two-line entries', () => {
    // height 10, 2 lines/entry, no header → 5 entries fit; 8 rows → scrolls.
    const w = computeWindow(8, 0, 2, 10, false);
    expect(w.moreBelow).toBe(true);
    // bottom … costs 1 line → floor((10-1)/2)=4 entries.
    expect(w.end).toBe(4);
  });
});

/**
 * SCROLLOFF = 1 spec (bug 4 — "the highlighted item must stay on screen; keep 1 row visible above
 * AND below the cursor, except at the list edges"). These pin the user's EXACT reported example and
 * the invariant `start <= cursor < end` at every cursor position, plus the list-edge exceptions and a
 * tiny-capacity degenerate case. This is the authoritative scroll test — the render tests can't catch
 * a live windowing bug because the measured height comes from the real terminal, not these budgets.
 *
 * The user's example: item1..item7 (7 rows, 0-indexed 0..6), a viewport that fits 3 entries plus a
 * top `…` and a bottom `…` (availableHeight 5, 1 line/entry, no header → with both indicators,
 * capacity = floor((5-1-1)/1) = 3). Cursor on item5 (index 4) shows `…item4 [item5] item6 …`; moving
 * to item6 (index 5) scrolls one row to `…item5 [item6] item7` (still a row visible below — here the
 * bottom edge, so the bottom `…` drops).
 */
describe('computeWindow — SCROLLOFF=1 spec (bug 4)', () => {
  const ROWS = 7; // item1..item7
  const H = 5; // fits 3 entries + top … + bottom … (capacity 3 when both indicators present)

  it("user's example: cursor on item5 (idx 4) shows item4..item6 with both … ", () => {
    const w = computeWindow(ROWS, 4, 1, H, false);
    expect(w).toEqual({ start: 3, end: 6, moreAbove: true, moreBelow: true });
    // 1 row visible above (item4=idx3) AND below (item6=idx5) the cursor (item5=idx4).
    expect(w.start).toBeLessThan(4);
    expect(w.end).toBeGreaterThan(5);
  });

  it("user's example: moving to item6 (idx 5) scrolls down, still shows a row above AND below", () => {
    const w = computeWindow(ROWS, 5, 1, H, false);
    // item7 (idx 6) is the last row, so the bottom margin is the list edge → the bottom `…` drops,
    // which frees a line and grows the window to 4 rows (item4..item7, idx 3..6). The cursor (item6,
    // idx 5) keeps a row visible above (item5, idx 4) and below (item7, idx 6) — the SCROLLOFF intent.
    expect(w).toEqual({ start: 3, end: 7, moreAbove: true, moreBelow: false });
    expect(w.start).toBeLessThan(5); // ≥1 row visible above the cursor
    expect(w.end).toBeGreaterThan(6); // ≥1 row visible below the cursor
    expect(5 >= w.start && 5 < w.end).toBe(true); // cursor on screen
  });

  it('cursor at row 0 has no top margin (start pinned to 0)', () => {
    const w = computeWindow(ROWS, 0, 1, H, false);
    expect(w.start).toBe(0);
    expect(w.moreAbove).toBe(false);
    expect(w.moreBelow).toBe(true);
    expect(0 >= w.start && 0 < w.end).toBe(true);
  });

  it('cursor at the last row has no bottom margin (end pinned to rowCount)', () => {
    const last = ROWS - 1;
    const w = computeWindow(ROWS, last, 1, H, false);
    expect(w.end).toBe(ROWS);
    expect(w.moreBelow).toBe(false);
    expect(w.moreAbove).toBe(true);
    expect(last >= w.start && last < w.end).toBe(true);
  });

  it('cursor in the middle keeps exactly one row above and one below', () => {
    for (let cursor = 1; cursor < ROWS - 1; cursor++) {
      const w = computeWindow(ROWS, cursor, 1, H, false);
      // Invariant: cursor strictly inside the window.
      expect(cursor >= w.start && cursor < w.end).toBe(true);
      // Interior cursor → a row visible both above and below (the SCROLLOFF=1 guarantee).
      expect(w.start).toBeLessThanOrEqual(cursor - 1);
      expect(w.end).toBeGreaterThanOrEqual(cursor + 2);
    }
  });

  it('cursor is ALWAYS within [start, end) for every position (sweep)', () => {
    for (let cursor = 0; cursor < ROWS; cursor++) {
      const w = computeWindow(ROWS, cursor, 1, H, false);
      expect(cursor >= w.start && cursor < w.end).toBe(true);
    }
  });

  it('walking j down one row at a time always keeps the cursor on screen', () => {
    // Simulate the exact j/k failure mode: at each step the highlighted row must be inside the window.
    for (let cursor = 0; cursor < ROWS; cursor++) {
      const w = computeWindow(ROWS, cursor, 1, H, false);
      expect(cursor).toBeGreaterThanOrEqual(w.start);
      expect(cursor).toBeLessThan(w.end);
    }
  });

  it('tiny capacity (height fits a single entry) still contains the cursor', () => {
    // height 1, 1 line/entry, no header: capacity collapses to 1; the window must be exactly the
    // cursor row (no room for scrolloff), never empty, cursor always inside.
    for (let cursor = 0; cursor < ROWS; cursor++) {
      const w = computeWindow(ROWS, cursor, 1, 1, false);
      expect(w.end - w.start).toBeGreaterThanOrEqual(1);
      expect(cursor >= w.start && cursor < w.end).toBe(true);
    }
  });

  it('with a header present, the cursor still stays on screen at every position', () => {
    // A header costs `linesPerEntry` lines when not scrolled; the top `…` replaces it when scrolled.
    for (let cursor = 0; cursor < ROWS; cursor++) {
      const w = computeWindow(ROWS, cursor, 1, H, true);
      expect(cursor >= w.start && cursor < w.end).toBe(true);
    }
  });
});

describe('Ledger — rendering', () => {
  it('renders the date field only when columns fit (collapse)', () => {
    // Ledger self-measures its OWN box width (ink-testing-library's measureElement returns real
    // dims here, ~100 cols, NOT zero), so to exercise column collapse the test bounds the WIDTH via
    // a wrapping Box rather than relying on the availableWidth fallback prop (which the measurement
    // overrides). A wide wrapper keeps both columns; a narrow one collapses to one.
    const wide = render(
      <Box width={80} height={10}>
        <Ledger
          rows={rows(2)}
          cursor={0}
          focused
          linesPerEntry={1}
          minColumns={1}
          maxColumns={2}
          renderEntry={renderDemo}
        />
      </Box>,
    );
    expect(wide.lastFrame()).toContain('d0');

    const narrow = render(
      <Box width={10} height={10}>
        <Ledger
          rows={rows(2)}
          cursor={0}
          focused
          linesPerEntry={1}
          minColumns={1}
          maxColumns={2}
          renderEntry={renderDemo}
        />
      </Box>,
    );
    const frame = narrow.lastFrame() ?? '';
    expect(frame).toContain('row0');
    expect(frame).not.toContain('d0');
  });

  it('shows … indicators when the list overflows the height budget', () => {
    const { lastFrame } = render(
      <Ledger
        rows={rows(20)}
        cursor={0}
        focused
        linesPerEntry={1}
        minColumns={1}
        maxColumns={1}
        availableHeight={5}
        availableWidth={40}
        renderEntry={renderDemo}
      />,
    );
    const frame = lastFrame() ?? '';
    // Top rows visible, a bottom … indicator, and rows past the window absent.
    expect(frame).toContain('row0');
    expect(frame).toContain('…');
    expect(frame).not.toContain('row19');
  });

  it('drops the header and shows a top … when scrolled past the top', () => {
    const header = (columns: number): React.ReactNode => (
      <Text>{columns >= 2 ? 'NAME DATE' : 'NAME'}</Text>
    );
    const top = render(
      <Ledger
        rows={rows(20)}
        cursor={0}
        focused
        linesPerEntry={1}
        minColumns={1}
        maxColumns={1}
        availableHeight={6}
        availableWidth={40}
        renderEntry={renderDemo}
        header={header}
      />,
    );
    // At the top the header shows.
    expect(top.lastFrame()).toContain('NAME');

    const scrolled = render(
      <Ledger
        rows={rows(20)}
        cursor={19}
        focused
        linesPerEntry={1}
        minColumns={1}
        maxColumns={1}
        availableHeight={6}
        availableWidth={40}
        renderEntry={renderDemo}
        header={header}
      />,
    );
    const frame = scrolled.lastFrame() ?? '';
    // Scrolled to the bottom: the header is gone, replaced by a top … indicator; cursor row shows.
    expect(frame).not.toContain('NAME');
    expect(frame).toContain('…');
    expect(frame).toContain('row19');
  });

  it('marks the cursor row as selected only when focused', () => {
    // The `▌` marker stands in for the (ANSI-stripped) full-width highlight: it appears on the
    // cursor row when focused and is absent when blurred (cursor remembered, drawn un-highlighted).
    const focused = render(
      <Ledger
        rows={rows(3)}
        cursor={1}
        focused
        linesPerEntry={1}
        minColumns={1}
        maxColumns={1}
        availableHeight={10}
        availableWidth={40}
        renderEntry={renderDemo}
      />,
    );
    const ffr = focused.lastFrame() ?? '';
    expect(ffr).toContain('▌');
    // The marker sits on row1 (the cursor row), not row0/row2.
    const lines = ffr.split('\n');
    expect(lines.find((l) => l.includes('row1'))).toContain('▌');
    expect(lines.find((l) => l.includes('row0'))).not.toContain('▌');

    const blurred = render(
      <Ledger
        rows={rows(3)}
        cursor={1}
        focused={false}
        linesPerEntry={1}
        minColumns={1}
        maxColumns={1}
        availableHeight={10}
        availableWidth={40}
        renderEntry={renderDemo}
      />,
    );
    expect(blurred.lastFrame()).not.toContain('▌');
  });
});

describe('Ledger — full-width highlight + alternating bg (forced color)', () => {
  // ink-testing-library strips ANSI from `lastFrame()` by default. These structural color props
  // (full-width selection bg, alternating row bg) only appear when color support is forced; the
  // suite doesn't run with FORCE_COLOR, so the visual is verified by-eye/probe and these assertions
  // run only when color is available. The behavioral focus-gating is covered above without color.
  const { FORCE_COLOR } = process.env;
  const colorOn = Boolean(FORCE_COLOR);
  it.skipIf(!colorOn)('paints a full-width selection bg + alternating odd-row bg', () => {
    // The Ledger colors come from `theme`: selection = `rowSelectedBg` (everforest bg_green #3c4841),
    // alternating = `rowAltBg` (everforest bg1 #2e383c). At truecolor these are the SGR codes below.
    const SELECTED_BG = '\x1b[48;2;60;72;65m'; // #3c4841
    const ALT_BG = '\x1b[48;2;46;56;60m'; // #2e383c
    const { lastFrame } = render(
      <Ledger
        rows={rows(4)}
        cursor={1}
        focused
        linesPerEntry={1}
        minColumns={1}
        maxColumns={1}
        availableHeight={10}
        availableWidth={40}
        renderEntry={renderDemo}
      />,
    );
    const frame = lastFrame() ?? '';
    expect(frame).toContain(SELECTED_BG);
    expect(frame).toContain(ALT_BG);
  });
});
