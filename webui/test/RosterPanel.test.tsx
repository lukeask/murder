/**
 * RosterPanel renders rows off a seeded `roster` slice (the read-side parity the port is about). We
 * write a ready roster directly to the store and assert the crow names + status badges paint. Also
 * checks the empty/loading hint when the slice has no rows.
 */

import type { RosterRow } from '@core/store/roster/rosterSlice.js';
import { screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup } from '@testing-library/react';
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
  status: 'working',
  session: 'murder_repo_collaborator_a1',
  ...over,
});

describe('RosterPanel', () => {
  it('renders crow rows from the roster slice', () => {
    const { store } = makeStore();
    seedSlice(store, 'roster', {
      rows: [row({ agentId: 'collab', status: 'working' }), row({ agentId: 'r1', role: 'rogue', status: 'idle' })],
      status: 'ready',
      error: null,
    });
    renderWithStore(<RosterPanel />, { store });
    expect(screen.getByText(/working/)).toBeTruthy();
    // At least one health dot + star control rendered.
    expect(document.querySelectorAll('.health').length).toBeGreaterThan(0);
    expect(document.querySelectorAll('.star').length).toBeGreaterThan(0);
  });

  it('shows the empty hint when the slice is ready with no rows', () => {
    const { store } = makeStore();
    seedSlice(store, 'roster', { rows: [], status: 'ready', error: null });
    renderWithStore(<RosterPanel />, { store });
    expect(screen.getByText('No agents.')).toBeTruthy();
  });
});
