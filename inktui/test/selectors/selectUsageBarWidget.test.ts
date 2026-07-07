/**
 * selectUsageBarWidget — shortest reset timer across selected harnesses for the usage bar widget.
 */

import { describe, expect, it } from 'vitest';
import { selectUsageBarWidget } from '../../src/selectors/selectUsageBarWidget.js';
import type { UsageRow } from '../../src/store/usage/usageSlice.js';

function row(overrides: Partial<UsageRow> = {}): UsageRow {
  return {
    harness: 'claude_code',
    windowKey: '5h',
    pct: 50,
    tUntilResetMinutes: 342,
    tPeriodMinutes: 300,
    steering: 'auto',
    ...overrides,
  };
}

function segmentText(
  segment: ReturnType<typeof selectUsageBarWidget>,
): string | undefined {
  return segment?.runs.map((run) => run.text).join('');
}

describe('selectUsageBarWidget', () => {
  it('two harnesses → the harness with the shorter reset wins', () => {
    const segment = selectUsageBarWidget(
      [
        row({ harness: 'claude_code', tUntilResetMinutes: 342 }),
        row({ harness: 'codex', tUntilResetMinutes: 90 }),
      ],
      undefined,
    );
    expect(segmentText(segment)).toBe('usage codex 1h30m');
  });

  it('collapses when there is no usage data', () => {
    expect(selectUsageBarWidget([], undefined)).toBeNull();
  });

  it('honors a single-harness selection', () => {
    const segment = selectUsageBarWidget(
      [
        row({ harness: 'claude_code', tUntilResetMinutes: 60 }),
        row({ harness: 'codex', tUntilResetMinutes: 10 }),
      ],
      ['claude_code'],
    );
    expect(segmentText(segment)).toBe('usage cc 1h');
  });

  it('formats a sub-hour timer as minutes', () => {
    const segment = selectUsageBarWidget([row({ tUntilResetMinutes: 42 })], undefined);
    expect(segmentText(segment)).toBe('usage cc 42m');
  });

  it('collapses when every row lacks a positive reset time', () => {
    expect(
      selectUsageBarWidget(
        [
          row({ tUntilResetMinutes: 0 }),
          row({ harness: 'codex', tUntilResetMinutes: -1 }),
        ],
        undefined,
      ),
    ).toBeNull();
  });

  it('picks the minimum across multiple windows for one harness', () => {
    const segment = selectUsageBarWidget(
      [
        row({ harness: 'codex', windowKey: '5h', tUntilResetMinutes: 200 }),
        row({ harness: 'codex', windowKey: 'weekly', tUntilResetMinutes: 45 }),
      ],
      ['codex'],
    );
    expect(segmentText(segment)).toBe('usage codex 45m');
  });
});
