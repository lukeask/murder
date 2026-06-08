/**
 * Reports selector tests — parallel to {@link ./notesSelectors.test.ts} (same DTO shape, same
 * presentation logic). Kept as a separate file because the two selectors are separate slices.
 */

import { selectReportsView } from '../../src/selectors/reportsSelectors.js';
import type { ReportRow, ReportsState } from '../../src/store/reports/reportsSlice.js';

function row(overrides: Partial<ReportRow> = {}): ReportRow {
  return {
    name: 'report-alpha',
    charCount: 5678,
    updatedAt: '2026-06-01T10:00:00',
    ...overrides,
  };
}

function state(rows: readonly ReportRow[], overrides: Partial<ReportsState> = {}): ReportsState {
  return { rows, status: 'ready', error: null, ...overrides };
}

describe('selectReportsView — presentation', () => {
  it('orders rows by updatedAt descending (most recent first), then name', () => {
    const view = selectReportsView(
      state([
        row({ name: 'b', updatedAt: '2026-05-01T00:00:00' }),
        row({ name: 'a', updatedAt: '2026-06-01T00:00:00' }),
        row({ name: 'd', updatedAt: '2026-04-01T00:00:00' }),
        row({ name: 'c', updatedAt: '2026-06-01T00:00:00' }),
      ]),
    );
    expect(view.rows.map((r) => r.name)).toEqual(['a', 'c', 'b', 'd']);
  });

  it('formats updatedAt as YYYY-MM-DD HH:MM', () => {
    const view = selectReportsView(state([row({ updatedAt: '2026-06-08T09:15:00.000' })]));
    expect(view.rows[0]?.updatedAt).toBe('2026-06-08 09:15');
  });

  it('formats charCount with "chars" suffix', () => {
    const view = selectReportsView(state([row({ charCount: 999 })]));
    expect(view.rows[0]?.charCount).toContain('chars');
    expect(view.rows[0]?.charCount).toContain('999');
  });

  it('carries load flags through and computes isEmpty', () => {
    expect(selectReportsView(state([])).isEmpty).toBe(true);
    expect(selectReportsView(state([row()])).isEmpty).toBe(false);
    const err = selectReportsView(state([], { status: 'error', error: 'fail' }));
    expect(err.status).toBe('error');
    expect(err.error).toBe('fail');
  });

  it('does not mutate the input slice (sorts a copy)', () => {
    const rows = [
      row({ name: 'b', updatedAt: '2026-05-01T00:00:00' }),
      row({ name: 'a', updatedAt: '2026-06-01T00:00:00' }),
    ];
    const original = [...rows];
    selectReportsView(state(rows));
    expect(rows).toEqual(original);
  });
});
