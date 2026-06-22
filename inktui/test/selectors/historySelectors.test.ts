/**
 * History selector tests — the loose-threads vs all view modes + relative-age formatting.
 */

import { describe, expect, it } from 'vitest';
import {
  formatRelativeAge,
  selectHistoryView,
} from '../../src/selectors/historySelectors.js';
import type { HistoryRow, HistoryState } from '../../src/store/history/historySlice.js';

function row(overrides: Partial<HistoryRow>): HistoryRow {
  return {
    itemId: 'c:0',
    text: 'an intention',
    target: 'collaborator',
    conversationId: 'c',
    ts: '2026-06-10T00:00:00',
    status: 'open',
    harness: null,
    conversationStatus: 'in_progress',
    resumable: false,
    ...overrides,
  };
}

function state(rows: HistoryRow[]): HistoryState {
  return { rows, status: 'ready', error: null };
}

const NOW = Date.parse('2026-06-12T00:00:00Z');

describe('selectHistoryView', () => {
  it('loose mode keeps only OPEN/STALE rows, newest first', () => {
    const s = state([
      row({ itemId: 'a', ts: '2026-06-11T00:00:00', status: 'open' }),
      row({ itemId: 'b', ts: '2026-06-09T00:00:00', status: 'stale' }),
      row({ itemId: 'c', ts: '2026-06-10T00:00:00', status: 'dismissed' }),
    ]);
    const view = selectHistoryView(s, 'loose', NOW);
    // dismissed dropped; remaining newest-first.
    expect(view.rows.map((r) => r.itemId)).toEqual(['a', 'b']);
    expect(view.looseCount).toBe(2);
  });

  it('all mode keeps every row, newest first', () => {
    const s = state([
      row({ itemId: 'a', ts: '2026-06-11T00:00:00', status: 'open' }),
      row({ itemId: 'b', ts: '2026-06-09T00:00:00', status: 'stale' }),
      row({ itemId: 'c', ts: '2026-06-10T00:00:00', status: 'dismissed' }),
    ]);
    const view = selectHistoryView(s, 'all', NOW);
    expect(view.rows.map((r) => r.itemId)).toEqual(['a', 'c', 'b']);
    // looseCount is mode-independent.
    expect(view.looseCount).toBe(2);
  });

  it('formats the status tag uppercase and reports isEmpty', () => {
    const view = selectHistoryView(state([]), 'loose', NOW);
    expect(view.isEmpty).toBe(true);
    const view2 = selectHistoryView(state([row({ status: 'stale' })]), 'loose', NOW);
    expect(view2.rows[0]?.statusTag).toBe('STALE');
  });
});

describe('formatRelativeAge', () => {
  it('renders compact relative ages', () => {
    const now = Date.parse('2026-06-12T12:00:00Z');
    expect(formatRelativeAge('2026-06-12T11:59:30Z', now)).toBe('just now');
    expect(formatRelativeAge('2026-06-12T11:30:00Z', now)).toBe('30m');
    expect(formatRelativeAge('2026-06-12T09:00:00Z', now)).toBe('3h');
    expect(formatRelativeAge('2026-06-10T12:00:00Z', now)).toBe('2d');
    expect(formatRelativeAge('not-a-date', now)).toBe('?');
  });
});
