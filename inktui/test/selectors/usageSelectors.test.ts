/**
 * usageSelectors tests — the view-model is a pure transform; no React, no store, no bus.
 *
 * Rule 2 proof: all formatting (pct label, period label, reset label, bar geometry, isHigh flag)
 * lives here. The component receives display-ready groups and does zero arithmetic — it only paints
 * colors from `isHigh` and the bar geometry (`filledCount` over `barWidth`).
 */

import { selectUsageView, USAGE_BAR_WIDTH } from '../../src/selectors/usageSelectors.js';
import type { UsageRow, UsageState } from '../../src/store/usage/usageSlice.js';

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
  return selectUsageView(state(rows)).groups[0]?.gauges[0];
}

describe('selectUsageView — formatting', () => {
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

  it('formats 0 reset time as "—"', () => {
    expect(firstGauge([row({ tUntilResetMinutes: 0 })])?.resetLabel).toBe('—');
  });

  it('isHigh true when pct >= 80', () => {
    expect(firstGauge([row({ pct: 80 })])?.isHigh).toBe(true);
    expect(firstGauge([row({ pct: 79.9 })])?.isHigh).toBe(false);
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
    );
    expect(view.groups.map((g) => g.harness)).toEqual(['codex', 'cursor']);
    expect(view.groups[0]?.gauges.map((g) => g.windowKey)).toEqual(['5h', 'weekly']);
    expect(view.groups[1]?.gauges).toHaveLength(1);
  });

  it('carries load flags through and computes isEmpty', () => {
    expect(selectUsageView(state([])).isEmpty).toBe(true);
    expect(selectUsageView(state([row()])).isEmpty).toBe(false);
    const err = selectUsageView(state([], { status: 'error', error: 'boom' }));
    expect(err.status).toBe('error');
    expect(err.error).toBe('boom');
  });

  it('does not mutate the input slice', () => {
    const rows = [row({ harness: 'b', pct: 30 }), row({ harness: 'a', pct: 70 })];
    const original = [...rows];
    selectUsageView(state(rows));
    expect(rows).toEqual(original);
  });
});
