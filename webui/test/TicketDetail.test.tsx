/**
 * TicketDetail (DS reskin) renders the open ticket off a seeded `ticketDetail` slice: the DS Panel
 * title (ticket Tag + id), the frontmatter key/value grid, the schedule DS Input, the body textarea,
 * and the primary save Button. Wired actions (close / save / schedule) stay intact.
 */

import { cleanup, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { TicketDetail } from '../src/components/stage/TicketDetail.js';
import { makeStore, renderWithStore, seedSlice } from './helpers.js';

afterEach(cleanup);

describe('TicketDetail (DS reskin)', () => {
  it('renders nothing when no ticket is open', () => {
    const { store } = makeStore();
    renderWithStore(<TicketDetail />, { store });
    expect(document.querySelector('.mds-ticket')).toBeNull();
  });

  it('renders the frontmatter grid, schedule input, and save button', () => {
    const { store } = makeStore();
    seedSlice(store, 'ticketDetail', {
      ticketId: 't001',
      frontmatter: {
        title: 'split orchestrator',
        status: 'in_progress',
        deps: '',
        harness: 'claude',
        model: 'opus',
        worktree: null,
        scheduleAt: null,
      },
      savedBody: '## body',
      editedBody: null,
      scheduleInput: '',
      scheduleValid: false,
      status: 'ready',
      error: null,
    });
    renderWithStore(<TicketDetail />, { store });

    expect(document.querySelector('.mds-ticket .mds-panel')).toBeTruthy();
    expect(screen.getByText('ticket')).toBeTruthy();
    expect(screen.getByText('t001')).toBeTruthy();
    // Frontmatter key/value grid.
    expect(screen.getByText('split orchestrator')).toBeTruthy();
    expect(screen.getByText('in_progress')).toBeTruthy();
    // Schedule DS Input + the body editor textarea + save Button.
    expect(document.querySelector('.mds-ticket__schedule .mds-input')).toBeTruthy();
    expect(document.querySelector('.mds-ticket__editor')).toBeTruthy();
    expect(screen.getByText('save body')).toBeTruthy();
  });
});
