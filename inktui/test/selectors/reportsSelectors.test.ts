/**
 * Reports selector tests — parallel to {@link ./notesSelectors.test.ts} (same DTO shape, same
 * presentation logic). Kept as a separate file because the two selectors are separate slices.
 */

import { selectReportsView } from '../../src/selectors/reportsSelectors.js';
import type { FavoritesState } from '../../src/store/favorites/favoritesSlice.js';
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

function favs(ids: readonly string[] = []): FavoritesState {
  return { ids: new Set(ids), status: 'ready', error: null };
}

const NO_FAVS = favs();

describe('selectReportsView — presentation', () => {
  it('orders rows by updatedAt descending (most recent first), then name', () => {
    const view = selectReportsView(
      state([
        row({ name: 'b', updatedAt: '2026-05-01T00:00:00' }),
        row({ name: 'a', updatedAt: '2026-06-01T00:00:00' }),
        row({ name: 'd', updatedAt: '2026-04-01T00:00:00' }),
        row({ name: 'c', updatedAt: '2026-06-01T00:00:00' }),
      ]),
      NO_FAVS,
    );
    expect(view.rows.map((r) => r.name)).toEqual(['a', 'c', 'b', 'd']);
  });

  it('formats updatedAt as the compact `Mon. dd HH:MM` (shared resourceMeta formatter)', () => {
    const view = selectReportsView(state([row({ updatedAt: '2026-06-08T09:15:00.000' })]), NO_FAVS);
    expect(view.rows[0]?.updatedAt).toBe('Jun. 08 09:15');
  });

  it('formats charCount with "chars" suffix', () => {
    const view = selectReportsView(state([row({ charCount: 999 })]), NO_FAVS);
    expect(view.rows[0]?.charCount).toContain('chars');
    expect(view.rows[0]?.charCount).toContain('999');
  });

  it('carries load flags through and computes isEmpty', () => {
    expect(selectReportsView(state([]), NO_FAVS).isEmpty).toBe(true);
    expect(selectReportsView(state([row()]), NO_FAVS).isEmpty).toBe(false);
    expect(selectReportsView(state([], { status: 'idle' }), NO_FAVS).status).toBe('ready');
    expect(selectReportsView(state([], { status: 'loading' }), NO_FAVS).status).toBe('loading');
    const err = selectReportsView(state([], { status: 'error', error: 'fail' }), NO_FAVS);
    expect(err.status).toBe('error');
    expect(err.error).toBe('fail');
  });

  it('does not mutate the input slice (sorts a copy)', () => {
    const rows = [
      row({ name: 'b', updatedAt: '2026-05-01T00:00:00' }),
      row({ name: 'a', updatedAt: '2026-06-01T00:00:00' }),
    ];
    const original = [...rows];
    selectReportsView(state(rows), NO_FAVS);
    expect(rows).toEqual(original);
  });

  it('floats starred reports to the top (rule 2)', () => {
    const view = selectReportsView(
      state([
        row({ name: 'recent', updatedAt: '2026-06-07T00:00:00' }),
        row({ name: 'starred-old', updatedAt: '2026-01-01T00:00:00' }),
      ]),
      favs(['starred-old']),
    );
    expect(view.rows.map((r) => r.name)).toEqual(['starred-old', 'recent']);
    expect(view.rows[0]?.starred).toBe(true);
  });
});
