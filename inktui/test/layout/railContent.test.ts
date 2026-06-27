/**
 * Natural-width source tests (L2) — the pure width functions only (the hook reads the store and is
 * exercised by App-level tests). These guard R2/R6/R8/R10:
 *  - empty list → 0 width;
 *  - one pathologically long name → capped, not inflating the rail (R8);
 *  - mixed small/large numeric values → alignment-stable width (R10), because the selectors padded
 *    the formatted strings equal.
 */

import { describe, expect, it } from 'vitest';
import { FILENAME_CAP } from '../../src/layout/budget.js';
import {
  crowNaturalHeight,
  crowNaturalWidth,
  docNaturalWidth,
  listNaturalHeight,
  ticketsNaturalWidth,
  titleRowWidth,
  usageNaturalHeight,
} from '../../src/layout/railContent.js';

// The selectors right-pad char counts to a fixed field width (15) so alignment holds. Mirror that
// here: both a "5,000" and a "50,000" row carry the SAME formatted-string length.
function padCharCount(n: number): string {
  return `${n.toLocaleString()} chars`.padEnd(15);
}

describe('docNaturalWidth (plans/notes/reports) — R2/R8/R10', () => {
  it('returns 0 for an empty list', () => {
    expect(docNaturalWidth([])).toBe(0);
  });

  it('measures the wider of the two formatted lines', () => {
    // Short name → line 2 (the metadata) is the wider line and sets the width.
    const rows = [{ name: 'a.md', charCount: padCharCount(5000), updatedAt: '2026-06-09 10:00' }];
    const w = docNaturalWidth(rows);
    const line2 = `    ${padCharCount(5000)} · 2026-06-09 10:00`;
    expect(w).toBe(line2.length);
  });

  it('caps a pathologically long name so it cannot inflate the rail (R8)', () => {
    const long = 'this-is-a-really-really-really-long-plan-filename-that-keeps-going.md';
    const rows = [{ name: long, charCount: padCharCount(1), updatedAt: '2026-06-09 10:00' }];
    const w = docNaturalWidth(rows);
    // Line 1 contributes AT MOST gutter(4) + the capped name (FILENAME_CAP) — the uncapped name
    // would have produced a far wider rail; confirm the cap bounds the contribution.
    expect(w).toBeLessThanOrEqual(
      Math.max(4 + FILENAME_CAP, `    ${padCharCount(1)} · 2026-06-09 10:00`.length),
    );
    // The uncapped name would have produced a far wider rail — confirm we did NOT inflate to it.
    expect(w).toBeLessThan(4 + long.length);
    // A SHORT name with the same metadata yields the SAME width — proof the long name didn't inflate it.
    const short = docNaturalWidth([
      { name: 'a.md', charCount: padCharCount(1), updatedAt: '2026-06-09 10:00' },
    ]);
    expect(w).toBe(short);
  });

  it('is alignment-stable: a "5,000" row and a "50,000" row produce the SAME width (R10)', () => {
    const small = [{ name: 'a.md', charCount: padCharCount(5000), updatedAt: '2026-06-09 10:00' }];
    const large = [{ name: 'a.md', charCount: padCharCount(50000), updatedAt: '2026-06-09 10:00' }];
    expect(docNaturalWidth(small)).toBe(docNaturalWidth(large));
  });

  it('takes the max over rows', () => {
    const rows = [
      { name: 'short.md', charCount: padCharCount(1), updatedAt: '2026-06-09 10:00' },
      {
        name: 'a-much-longer-but-under-cap-name.md',
        charCount: padCharCount(1),
        updatedAt: '2026-06-09 10:00',
      },
    ];
    // The longer (still under-cap-or-equal) name's line 1 should dominate.
    const w = docNaturalWidth(rows);
    expect(w).toBeGreaterThanOrEqual(
      4 + Math.min('a-much-longer-but-under-cap-name.md'.length, FILENAME_CAP),
    );
  });
});

describe('ticketsNaturalWidth — multi-column block (R3)', () => {
  function ticket(over: Partial<Record<string, string>> = {}) {
    return {
      idCell: 'T-1',
      titleCell: 'Title',
      statusCell: 'open',
      lastUpdateCell: '2026-06-09 now',
      depsCell: 'ok',
      scheduleCell: '—',
      harnessCell: 'claude',
      modelCell: 'opus',
      planCell: '—',
      worktreeCell: '—',
      ...over,
    };
  }

  it('returns 0 for an empty list', () => {
    expect(ticketsNaturalWidth([])).toBe(0);
  });

  it('sums the five column widths plus gutter + inter-column gaps', () => {
    const w = ticketsNaturalWidth([ticket()]);
    // col1 max(3,5)=5, col2 max(4,14)=14, col3 max(2,1)=2, col4 max(6,4)=6, col5 max(1,1)=1
    // gutter(2) + 4 gaps + (5+14+2+6+1) = 2 + 4 + 28 = 34
    expect(w).toBe(34);
  });

  it('widens with a long title cell (the wider of a column wins)', () => {
    const narrow = ticketsNaturalWidth([ticket({ titleCell: 'x' })]);
    const wide = ticketsNaturalWidth([ticket({ titleCell: 'a-much-longer-ticket-title' })]);
    expect(wide).toBeGreaterThan(narrow);
  });
});

describe('crowNaturalWidth — right rail (R6/R8)', () => {
  function section(rows: { name: string; status: string; harness: string; model: string }[]) {
    return { label: 'Collaborator', rows };
  }

  it('returns 0 for no sections', () => {
    expect(crowNaturalWidth([], false)).toBe(0);
  });

  it('accounts for the section header label width', () => {
    // A wide group label with no rows → the label sets the width.
    const w = crowNaturalWidth([{ label: 'Planning Agents', rows: [] }], false);
    expect(w).toBe('Planning Agents'.length);
  });

  it('measures glyph + name + status on line 1 (minimized)', () => {
    const w = crowNaturalWidth(
      [section([{ name: 'crow-a', status: 'running', harness: 'claude', model: 'opus' }])],
      false,
    );
    // glyph(1)+space(1) + name(6) + gap(2) + status(7) = 17 ; header "Collaborator" = 12 → 17 wins.
    expect(w).toBe(17);
  });

  it('the maximized second line can widen the rail (harness · model)', () => {
    const minimized = crowNaturalWidth(
      [section([{ name: 'c', status: 'ok', harness: 'claude-code', model: 'a-long-model-name' }])],
      false,
    );
    const maximized = crowNaturalWidth(
      [section([{ name: 'c', status: 'ok', harness: 'claude-code', model: 'a-long-model-name' }])],
      true,
    );
    expect(maximized).toBeGreaterThan(minimized);
  });

  it('caps a long crow name (R8)', () => {
    const longName = 'a-really-long-crow-session-name-that-goes-on-forever';
    const w = crowNaturalWidth(
      [section([{ name: longName, status: 'x', harness: 'h', model: 'm' }])],
      false,
    );
    // glyph(1)+space(1) + capped name + gap(2) + status(1)
    expect(w).toBe(2 + FILENAME_CAP + 2 + 1);
    expect(w).toBeLessThan(2 + longName.length + 2 + 1);
  });
});

describe('titleRowWidth — inline-title border row (L3b)', () => {
  it('is `╭─ ` + title + ` ╮` for a plain title', () => {
    // chrome = '╭─ '(3) + ' ╮'(2) = 5; 'Plans'(5) → 10.
    expect(titleRowWidth('Plans')).toBe(5 + 'Plans'.length);
    expect(titleRowWidth('Plans')).toBe(10);
  });

  it('adds titleExtra length when provided', () => {
    const withExtra = titleRowWidth('Crows', ' [extra]');
    const without = titleRowWidth('Crows');
    expect(withExtra).toBeGreaterThan(without);
    expect(withExtra).toBe(5 + 'Crows'.length + ' [extra]'.length);
  });

  it('a long-title / short-content panel: the rail must be ≥ its title row (L3b)', () => {
    const tinyBodyRow = crowNaturalWidth([{ label: 'X', rows: [] }], false); // == 1 (label 'X')
    const naturalWidth = Math.max(tinyBodyRow, titleRowWidth('Crows'));
    expect(naturalWidth).toBeGreaterThanOrEqual(titleRowWidth('Crows'));
  });
});

describe('listNaturalHeight — portrait content height for Ledger panels (L4b)', () => {
  it('counts borders(2) + header(linesPerEntry) + rows×linesPerEntry', () => {
    // 3 rows, 2 lines each, with a column-titles header: 2 + 2 + 3*2 = 10.
    expect(listNaturalHeight(3, 2, true)).toBe(2 + 2 + 6);
  });

  it('drops the header lines when the panel has no column-titles header', () => {
    expect(listNaturalHeight(3, 2, false)).toBe(2 + 0 + 6);
  });

  it('an empty list is borders(2) + one chrome line (e.g. "no plans")', () => {
    expect(listNaturalHeight(0, 2, true)).toBe(2 + 1);
    expect(listNaturalHeight(0, 1, false)).toBe(2 + 1);
  });

  it('does not double-count: the title row IS the top border (only 2 border lines total)', () => {
    // One single-line row, no header: 2 borders + 1 row = 3 — NOT 4 (a separate title + top border).
    expect(listNaturalHeight(1, 1, false)).toBe(3);
  });
});

describe('crowNaturalHeight — flattened sections + headers (L4b)', () => {
  function section(n: number) {
    return {
      rows: Array.from({ length: n }, () => ({})),
    };
  }

  it('counts the section-header rows AND the crow rows (flattened Ledger)', () => {
    // Two sections of 2 + 1 crows → flat rows = (1 header + 2) + (1 header + 1) = 5 rows.
    // minimized linesPerEntry=1, with the "crow · status" header: 2 + 1 + 5*1 = 8.
    expect(crowNaturalHeight([section(2), section(1)], false)).toBe(2 + 1 + 5);
  });

  it('maximized (linesPerEntry=2) is taller than minimized', () => {
    const min = crowNaturalHeight([section(3)], false);
    const max = crowNaturalHeight([section(3)], true);
    expect(max).toBeGreaterThan(min);
  });

  it('no sections → borders(2) + one "no crows" chrome line', () => {
    expect(crowNaturalHeight([], false)).toBe(2 + 1);
  });
});

describe('usageNaturalHeight — non-Ledger gauge block (L4b)', () => {
  function group(gauges: number) {
    return { gauges: Array.from({ length: gauges }, () => ({})) };
  }

  it('counts borders(2) + key line(1) + per-group (header + gauge lines)', () => {
    // One group with 2 gauges: 2 + 1(key) + 1(header) + 2(gauges) = 6.
    expect(usageNaturalHeight([group(2)])).toBe(2 + 1 + 1 + 2);
  });

  it('sums across multiple provider groups', () => {
    // g1: header + 2 gauges; g2: header + 1 gauge → key(1) + (1+2) + (1+1) = 6 body + 2 borders = 8.
    expect(usageNaturalHeight([group(2), group(1)])).toBe(2 + 1 + 3 + 2);
  });

  it('empty usage → borders(2) + one "no usage data" chrome line', () => {
    expect(usageNaturalHeight([])).toBe(2 + 1);
  });
});
