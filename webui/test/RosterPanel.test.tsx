/**
 * RosterPanel (DS reskin) renders rows off a seeded `roster` slice. We write a ready roster directly
 * to the store and assert the DS composition: the DS Panel, a ListRow per crow (with its Avatar +
 * name), the health StatusDot, the favorite star toggle, and the empty hint. Mirrors the TicketsPanel
 * exemplar smoke test (C2). Reset for ticket-bound crows goes through a confirm dialog.
 */

import type { RosterRow } from '@murder/ui-core/store/roster/rosterSlice.js';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RosterPanel } from '../src/components/panels/RosterPanel.js';
import { makeStore, renderWithStore, seedSlice } from './helpers.js';

afterEach(cleanup);

const row = (over: Partial<RosterRow>): RosterRow => ({
  agentId: 'a1',
  role: 'collaborator',
  ticketId: null,
  ticketTitle: null,
  harness: 'claude',
  model: 'opus',
  status: 'running',
  session: 'murder_repo_collaborator_a1',
  ...over,
});

describe('RosterPanel (DS reskin)', () => {
  it('renders crow rows with the DS Panel + ListRow + StatusDot', () => {
    const { store } = makeStore();
    seedSlice(store, 'roster', {
      rows: [
        row({ agentId: 'collab', status: 'running' }),
        // role 'crow' with no ticketId → rogue group.
        row({ agentId: 'r1', role: 'crow', ticketId: null, status: 'idle', session: null }),
      ],
      status: 'ready',
      error: null,
    });
    renderWithStore(<RosterPanel />, { store });

    // DS Panel container + title.
    expect(document.querySelector('.mds-panel')).toBeTruthy();
    expect(screen.getByText('crows')).toBeTruthy();
    // Rows render as DS ListRows.
    expect(document.querySelectorAll('.mds-row').length).toBeGreaterThan(0);
    // Each crow row carries an Avatar identity tile.
    expect(document.querySelectorAll('.mds-avatar').length).toBeGreaterThan(0);
    // Health surfaces as a DS StatusDot; the favorite star is the ListRow's own pin toggle.
    expect(document.querySelectorAll('.mds-statusdot').length).toBeGreaterThan(0);
    expect(document.querySelectorAll('.mds-row__star').length).toBeGreaterThan(0);
    // The raw status word labels the dot.
    expect(screen.getByText(/running/)).toBeTruthy();
  });

  it('shows the empty hint when the slice is ready with no rows', () => {
    const { store } = makeStore();
    seedSlice(store, 'roster', { rows: [], status: 'ready', error: null });
    renderWithStore(<RosterPanel />, { store });
    expect(screen.getByText('No agents.')).toBeTruthy();
  });

  it('confirms before resetting a ticket-bound crow', async () => {
    const { store, bus } = makeStore();
    const reset = vi.fn(() => ({ ok: true }));
    bus.stubCommand('crow.reset', reset);
    seedSlice(store, 'roster', {
      rows: [
        row({
          agentId: 't1',
          role: 'crow',
          ticketId: 'ticket-9',
          status: 'running',
          session: 's1',
        }),
      ],
      status: 'ready',
      error: null,
    });
    renderWithStore(<RosterPanel />, { store, bus });

    fireEvent.click(screen.getByLabelText('Reset s1'));
    expect(screen.getByRole('dialog')).toBeTruthy();
    expect(reset).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    await waitFor(() => expect(reset).toHaveBeenCalled());
    expect(bus.commandCalls).toEqual([
      { name: 'crow.reset', params: { ticket_id: 'ticket-9' } },
    ]);
  });
});
