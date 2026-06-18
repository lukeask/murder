/**
 * HistoryPanel (DS reskin) renders rows off a seeded `history` slice. We write a ready history slice
 * directly to the store and assert the DS composition: the DS Panel + loose/all Tabs in the actions
 * slot, a ListRow per item with its status Tag, and the empty hint. Mirrors the TicketsPanel exemplar
 * smoke test (C2).
 */

import type { HistoryRow } from '@core/store/history/historySlice.js';
import { cleanup, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { HistoryPanel } from '../src/components/panels/HistoryPanel.js';
import { makeStore, renderWithStore, seedSlice } from './helpers.js';

afterEach(cleanup);

const row = (over: Partial<HistoryRow>): HistoryRow => ({
  itemId: 'h1',
  text: 'plan the orchestrator split',
  target: 'collab',
  ts: '2026-06-15T01:00:00Z',
  status: 'open',
  harness: 'claude',
  conversationStatus: 'open',
  resumable: true,
  ...over,
});

describe('HistoryPanel (DS reskin)', () => {
  it('renders history rows with the DS Panel + Tabs + Tag', () => {
    const { store } = makeStore();
    seedSlice(store, 'history', {
      rows: [row({ itemId: 'h1', text: 'plan the orchestrator split', status: 'open' })],
      status: 'ready',
      error: null,
    });
    renderWithStore(<HistoryPanel />, { store });

    // DS Panel container + title.
    expect(document.querySelector('.mds-panel')).toBeTruthy();
    expect(screen.getByText('History')).toBeTruthy();
    // The loose/all filter rides the Panel actions slot as DS Tabs.
    expect(document.querySelector('.mds-tabs')).toBeTruthy();
    // The row renders as a DS ListRow with its text + status Tag.
    expect(document.querySelector('.mds-row')).toBeTruthy();
    expect(screen.getByText(/orchestrator split/)).toBeTruthy();
    expect(document.querySelector('.mds-tag')).toBeTruthy();
    expect(screen.getByText('OPEN')).toBeTruthy();
  });

  it('shows the empty hint when the slice is ready with no rows', () => {
    const { store } = makeStore();
    // 'all' mode default is loose, which filters to open/stale — seed an open row would show; use a
    // dismissed row so the default loose view is empty.
    seedSlice(store, 'history', {
      rows: [row({ itemId: 'd1', status: 'dismissed', resumable: false })],
      status: 'ready',
      error: null,
    });
    renderWithStore(<HistoryPanel />, { store });
    expect(screen.getByText('No history.')).toBeTruthy();
  });
});
