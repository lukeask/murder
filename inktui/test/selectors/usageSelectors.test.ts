/**
 * usageSelectors tests — the view-model is a pure transform; no React, no store, no bus.
 *
 * Rule 2 proof: all formatting (pct label, bar, reset label, isHigh flag) lives here.
 * The component receives pre-formatted strings and does zero arithmetic.
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
    ...overrides,
  };
}

function state(rows: readonly UsageRow[], overrides: Partial<UsageState> = {}): UsageState {
  return { rows, status: 'ready', error: null, ...overrides };
}

describe('selectUsageView — formatting', () => {
  it('formats pct as a rounded percentage label', () => {
    const view = selectUsageView(state([row({ pct: 73.4 })]));
    expect(view.rows[0]?.pctLabel).toBe('73%');
  });

  it('builds a bar of exactly USAGE_BAR_WIDTH characters', () => {
    const view = selectUsageView(state([row({ pct: 50 })]));
    expect(view.rows[0]?.bar).toHaveLength(USAGE_BAR_WIDTH);
  });

  it('bar is all filled at 100%', () => {
    const view = selectUsageView(state([row({ pct: 100 })]));
    expect(view.rows[0]?.bar).not.toContain('░');
  });

  it('bar is all empty at 0%', () => {
    const view = selectUsageView(state([row({ pct: 0 })]));
    expect(view.rows[0]?.bar).not.toContain('█');
  });

  it('formats reset minutes as "Xm" for < 60 minutes', () => {
    const view = selectUsageView(state([row({ tUntilResetMinutes: 4.2 })]));
    expect(view.rows[0]?.resetLabel).toBe('5m'); // ceil(4.2) = 5
  });

  it('formats reset minutes as "Xh" for whole hours', () => {
    const view = selectUsageView(state([row({ tUntilResetMinutes: 120 })]));
    expect(view.rows[0]?.resetLabel).toBe('2h');
  });

  it('formats reset minutes as "XhYm" for non-whole hours', () => {
    const view = selectUsageView(state([row({ tUntilResetMinutes: 90 })]));
    expect(view.rows[0]?.resetLabel).toBe('1h30m');
  });

  it('formats 0 reset time as "—"', () => {
    const view = selectUsageView(state([row({ tUntilResetMinutes: 0 })]));
    expect(view.rows[0]?.resetLabel).toBe('—');
  });

  it('isHigh true when pct >= 80', () => {
    const high = selectUsageView(state([row({ pct: 80 })]));
    expect(high.rows[0]?.isHigh).toBe(true);

    const notHigh = selectUsageView(state([row({ pct: 79.9 })]));
    expect(notHigh.rows[0]?.isHigh).toBe(false);
  });

  it('sorts rows by pct descending', () => {
    const view = selectUsageView(
      state([
        row({ harness: 'codex', pct: 30 }),
        row({ harness: 'claude', pct: 70 }),
        row({ harness: 'cursor', pct: 50 }),
      ]),
    );
    const harnessOrder = view.rows.map((r) => r.harness);
    expect(harnessOrder).toEqual(['claude', 'cursor', 'codex']);
  });

  it('sorts by harness name for equal pct (stable tiebreak)', () => {
    const view = selectUsageView(
      state([row({ harness: 'z-harness', pct: 50 }), row({ harness: 'a-harness', pct: 50 })]),
    );
    expect(view.rows[0]?.harness).toBe('a-harness');
    expect(view.rows[1]?.harness).toBe('z-harness');
  });

  it('carries load flags through and computes isEmpty', () => {
    expect(selectUsageView(state([])).isEmpty).toBe(true);
    expect(selectUsageView(state([row()])).isEmpty).toBe(false);
    const err = selectUsageView(state([], { status: 'error', error: 'boom' }));
    expect(err.status).toBe('error');
    expect(err.error).toBe('boom');
  });

  it('does not mutate the input slice (sorts a copy)', () => {
    const rows = [row({ harness: 'b', pct: 30 }), row({ harness: 'a', pct: 70 })];
    const original = [...rows];
    selectUsageView(state(rows));
    expect(rows).toEqual(original);
  });
});
