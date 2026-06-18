/**
 * UsagePanel (Phase C2 DS reskin) smoke test — mirrors the TicketsPanel exemplar. Seeds a ready
 * `usage` slice directly to the store and asserts: the DS Panel title, the harness header, a gauge
 * meter + pct label, the DS Select steering control, and the empty hint. Data wiring (selectUsageView
 * grouping) is exercised by core; this checks the DS composition renders.
 */

import type { UsageRow } from '@core/store/usage/usageSlice.js';
import { cleanup, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { UsagePanel } from '../src/components/panels/UsagePanel.js';
import { makeStore, renderWithStore, seedSlice } from './helpers.js';

afterEach(cleanup);

const row = (over: Partial<UsageRow>): UsageRow => ({
  harness: 'claude',
  windowKey: '5h',
  pct: 42,
  tUntilResetMinutes: 112,
  tPeriodMinutes: 300,
  steering: 'auto',
  ...over,
});

describe('UsagePanel (DS reskin)', () => {
  it('renders usage groups with the DS Panel + tokenized meter', () => {
    const { store } = makeStore();
    seedSlice(store, 'usage', {
      rows: [
        row({ windowKey: '5h', pct: 42 }),
        row({ windowKey: 'weekly', pct: 88 }),
      ],
      status: 'ready',
      error: null,
    });
    renderWithStore(<UsagePanel />, { store });

    // DS Panel container + title.
    expect(document.querySelector('.mds-panel')).toBeTruthy();
    expect(screen.getByText('usage')).toBeTruthy();
    // The harness header + at least one tokenized meter.
    expect(screen.getByText('claude')).toBeTruthy();
    expect(document.querySelector('.usage-meter')).toBeTruthy();
    expect(document.querySelector('.usage-meter__fill')).toBeTruthy();
    // High window (>=80%) paints the high fill.
    expect(document.querySelector('.usage-meter__fill--high')).toBeTruthy();
    // Steering renders as a DS Select.
    expect(document.querySelector('.mds-select')).toBeTruthy();
    // Pct label is formatted by the selector.
    expect(screen.getByText('42%')).toBeTruthy();
  });

  it('shows the empty hint when the slice is ready with no rows', () => {
    const { store } = makeStore();
    seedSlice(store, 'usage', { rows: [], status: 'ready', error: null });
    renderWithStore(<UsagePanel />, { store });
    expect(screen.getByText('No usage data.')).toBeTruthy();
  });
});
