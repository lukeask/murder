/**
 * RosterPanel (DS reskin) renders rows off a seeded `roster` slice. We write a ready roster directly
 * to the store and assert the DS composition: the DS Panel, a ListRow per crow (with its Avatar +
 * name), the health StatusDot, the favorite star toggle, and the empty hint. Mirrors the TicketsPanel
 * exemplar smoke test (C2).
 */

import type { RosterRow } from '@core/store/roster/rosterSlice.js';
import { cleanup, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
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
    expect(screen.getByText('Crows')).toBeTruthy();
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
});
