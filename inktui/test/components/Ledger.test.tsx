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

describe('Ledger — rendering', () => {
  it('renders the date field only when columns fit (collapse)', () => {
    const wide = render(
      <Ledger
        rows={rows(2)}
        cursor={0}
        focused
        linesPerEntry={1}
        minColumns={1}
        maxColumns={2}
        availableHeight={10}
        availableWidth={80}
        renderEntry={renderDemo}
      />,
    );
    expect(wide.lastFrame()).toContain('d0');

    const narrow = render(
      <Ledger
        rows={rows(2)}
        cursor={0}
        focused
        linesPerEntry={1}
        minColumns={1}
        maxColumns={2}
        availableHeight={10}
        availableWidth={10}
        renderEntry={renderDemo}
      />,
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
    const BLUE_BG = '\x1b[44m';
    const ALT_BG = '\x1b[48;2;30;30;46m'; // #1e1e2e
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
    expect(frame).toContain(BLUE_BG);
    expect(frame).toContain(ALT_BG);
  });
});
