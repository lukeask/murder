/**
 * TicketsPanel (reskinned, DS exemplar) renders rows off a seeded `tickets` slice. We write a ready
 * tickets slice directly to the store and assert: the DS Panel title, the title cell, the status
 * Badge, and the empty hint. This is the C1 exemplar smoke test the C2 panel reskins mirror.
 */

import type { TicketRow } from '@core/store/tickets/ticketsSlice.js';
import { cleanup, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { TicketsPanel } from '../src/components/panels/TicketsPanel.js';
import { makeStore, renderWithStore, seedSlice } from './helpers.js';

afterEach(cleanup);

const row = (over: Partial<TicketRow>): TicketRow => ({
  id: 't001',
  title: 'split orchestrator',
  status: 'done',
  lastUpdateAt: '2026-06-15T01:00:00Z',
  lastUpdateLabel: 'agent summarized',
  scheduleAt: null,
  harness: 'claude',
  model: 'opus',
  pendingDepIds: [],
  parent: null,
  ...over,
});

describe('TicketsPanel (DS reskin)', () => {
  it('renders ticket rows with the DS Panel + Badge', () => {
    const { store } = makeStore();
    seedSlice(store, 'tickets', {
      rows: [row({ id: 't001', title: 'split orchestrator', status: 'done' })],
      status: 'ready',
      error: null,
    });
    renderWithStore(<TicketsPanel />, { store });

    // DS Panel container + title.
    expect(document.querySelector('.mds-panel')).toBeTruthy();
    expect(screen.getByText('tickets')).toBeTruthy();
    // The title cell renders inside a DS ListRow.
    expect(document.querySelector('.mds-row')).toBeTruthy();
    expect(screen.getByText(/split orchestrator/)).toBeTruthy();
    // Status surfaces as a DS Badge (the done tone).
    expect(document.querySelector('.mds-badge')).toBeTruthy();
  });

  it('shows the empty hint when the slice is ready with no rows', () => {
    const { store } = makeStore();
    seedSlice(store, 'tickets', { rows: [], status: 'ready', error: null });
    renderWithStore(<TicketsPanel />, { store });
    expect(screen.getByText('No tickets.')).toBeTruthy();
  });
});
