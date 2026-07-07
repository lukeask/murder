/**
 * usageSelectors tests — the view-model is a pure transform; no React, no store, no bus.
 *
 * Rule 2 proof: all formatting (pct label, period label, reset label, bar geometry, isHigh flag)
 * lives here. The component receives display-ready groups and does zero arithmetic — it only paints
 * colors from `isHigh` and the bar geometry (`filledCount` over `barWidth`).
 */

import {
  formatRelativeFetchedAt,
  selectUsageView,
  USAGE_BAR_WIDTH,
} from '../../src/selectors/usageSelectors.js';
import type { UsageRow, UsageState } from '../../src/store/usage/usageSlice.js';

const NOW = Date.parse('2026-06-09T12:00:00');

function row(overrides: Partial<UsageRow> = {}): UsageRow {
  return {
    harness: 'claude',
    windowKey: 'h1',
    pct: 50,
    tUntilResetMinutes: 10,
    tPeriodMinutes: 60,
    steering: 'auto',
    ...overrides,
  };
}

function state(rows: readonly UsageRow[], overrides: Partial<UsageState> = {}): UsageState {
  return { rows, status: 'ready', error: null, ...overrides };
}

/** First gauge of the first group — the common single-row assertion target. */
function firstGauge(rows: readonly UsageRow[]) {
  return selectUsageView(state(rows), NOW).groups[0]?.gauges[0];
}

function firstGroup(rows: readonly UsageRow[]) {
  return selectUsageView(state(rows), NOW).groups[0];
}

describe('selectUsageView — formatting', () => {
  it('formats fetchedAt as a relative timestamp on the harness group', () => {
    expect(
      firstGroup([row({ fetchedAt: '2026-06-09T11:58:00' })])?.fetchedAtLabel,
    ).toBe('2m ago');
    expect(
      firstGroup([row({ fetchedAt: '2026-06-09T09:00:00' })])?.fetchedAtLabel,
    ).toBe('3h ago');
    expect(
      firstGroup([row({ fetchedAt: '2026-06-07T12:00:00' })])?.fetchedAtLabel,
    ).toBe('2d ago');
  });

  it('formats pct as a rounded percentage label', () => {
    expect(firstGauge([row({ pct: 73.4 })])?.pctLabel).toBe('73%');
  });

  it('reports a bar width of exactly USAGE_BAR_WIDTH', () => {
    expect(firstGauge([row({ pct: 50 })])?.barWidth).toBe(USAGE_BAR_WIDTH);
  });

  it('filledCount is the whole width at 100%', () => {
    expect(firstGauge([row({ pct: 100 })])?.filledCount).toBe(USAGE_BAR_WIDTH);
  });

  it('filledCount is 0 at 0%', () => {
    expect(firstGauge([row({ pct: 0 })])?.filledCount).toBe(0);
  });

  it('formats the window length as a period label (days / hours)', () => {
    expect(firstGauge([row({ tPeriodMinutes: 7 * 24 * 60 })])?.periodLabel).toBe('7d');
    expect(firstGauge([row({ tPeriodMinutes: 5 * 60 })])?.periodLabel).toBe('5h');
    expect(firstGauge([row({ tPeriodMinutes: 30 * 24 * 60 })])?.periodLabel).toBe('30d');
    expect(firstGauge([row({ tPeriodMinutes: 0 })])?.periodLabel).toBe('');
  });

  it('formats reset minutes as "Xm" for < 60 minutes', () => {
    expect(firstGauge([row({ tUntilResetMinutes: 4.2 })])?.resetLabel).toBe('5m'); // ceil(4.2) = 5
  });

  it('formats reset minutes as "Xh" for whole hours', () => {
    expect(firstGauge([row({ tUntilResetMinutes: 120 })])?.resetLabel).toBe('2h');
  });

  it('formats reset minutes as "XhYm" for non-whole hours', () => {
    expect(firstGauge([row({ tUntilResetMinutes: 90 })])?.resetLabel).toBe('1h30m');
  });

  it('formats reset minutes as "XhYm" under 48h (e.g. 24h43m)', () => {
    expect(firstGauge([row({ tUntilResetMinutes: 24 * 60 + 43 })])?.resetLabel).toBe('24h43m');
    expect(firstGauge([row({ tUntilResetMinutes: 4 * 60 + 35 })])?.resetLabel).toBe('4h35m');
  });

  it('formats long resets as "Xd" / "XdYh" (hours rounded up, no minutes)', () => {
    expect(firstGauge([row({ tUntilResetMinutes: 152 * 60 + 25 })])?.resetLabel).toBe('6d9h');
    expect(firstGauge([row({ tUntilResetMinutes: 48 * 60 })])?.resetLabel).toBe('2d');
    expect(firstGauge([row({ tUntilResetMinutes: 50 * 60 + 30 })])?.resetLabel).toBe('2d3h');
  });

  it('formats 0 reset time as "—"', () => {
    expect(firstGauge([row({ tUntilResetMinutes: 0 })])?.resetLabel).toBe('—');
  });

  it('isHigh true when pct >= 80', () => {
    expect(firstGauge([row({ pct: 80 })])?.isHigh).toBe(true);
    expect(firstGauge([row({ pct: 79.9 })])?.isHigh).toBe(false);
  });
});

describe('formatRelativeFetchedAt — edge cases', () => {
  it('omits the label when fetchedAt is missing or unparseable', () => {
    expect(formatRelativeFetchedAt(undefined, NOW)).toBeUndefined();
    expect(formatRelativeFetchedAt(null, NOW)).toBeUndefined();
    expect(formatRelativeFetchedAt('', NOW)).toBeUndefined();
    expect(formatRelativeFetchedAt('not-a-date', NOW)).toBeUndefined();
    expect(firstGroup([row()])?.fetchedAtLabel).toBeUndefined();
  });

  it('formats sub-minute ages as "just now"', () => {
    expect(formatRelativeFetchedAt('2026-06-09T11:59:30', NOW)).toBe('just now');
    expect(firstGroup([row({ fetchedAt: '2026-06-09T11:59:30' })])?.fetchedAtLabel).toBe(
      'just now',
    );
  });

  it('formats multi-day ages as "Xd ago"', () => {
    expect(formatRelativeFetchedAt('2026-06-04T12:00:00', NOW)).toBe('5d ago');
  });
});

describe('selectUsageView — grouping', () => {
  it('groups windows under their harness in first-seen order', () => {
    const view = selectUsageView(
      state([
        row({ harness: 'codex', windowKey: '5h', pct: 30 }),
        row({ harness: 'codex', windowKey: 'weekly', pct: 70 }),
        row({ harness: 'cursor', windowKey: 'api', pct: 50 }),
      ]),
      NOW,
    );
    expect(view.groups.map((g) => g.harness)).toEqual(['codex', 'cursor']);
    expect(view.groups[0]?.gauges.map((g) => g.windowKey)).toEqual(['5h', 'weekly']);
    expect(view.groups[1]?.gauges).toHaveLength(1);
  });

  it('carries load flags through and computes isEmpty', () => {
    expect(selectUsageView(state([]), NOW).isEmpty).toBe(true);
    expect(selectUsageView(state([row()]), NOW).isEmpty).toBe(false);
    const err = selectUsageView(state([], { status: 'error', error: 'boom' }), NOW);
    expect(err.status).toBe('error');
    expect(err.error).toBe('boom');
  });

  it('does not mutate the input slice', () => {
    const rows = [row({ harness: 'b', pct: 30 }), row({ harness: 'a', pct: 70 })];
    const original = [...rows];
    selectUsageView(state(rows), NOW);
    expect(rows).toEqual(original);
  });
});
