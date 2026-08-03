/**
 * TicketDetail (DS reskin) renders the open ticket off a seeded `ticketDetail` slice: the DS Panel
 * title (ticket Tag + id), the frontmatter key/value grid (incl. scheduleAt), checklist toggles,
 * the schedule DS Input, the body textarea, and the primary save Button (save+schedule+close).
 */

import { cleanup, fireEvent, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { TicketDetail } from '../src/components/stage/TicketDetail.js';
import { makeStore, renderWithStore, seedSlice } from './helpers.js';

afterEach(cleanup);

describe('TicketDetail (DS reskin)', () => {
  it('renders nothing when no ticket is open', () => {
    const { store } = makeStore();
    renderWithStore(<TicketDetail />, { store });
    expect(document.querySelector('.mds-ticket')).toBeNull();
  });

  it('renders the frontmatter grid, scheduleAt, checklist, and save button', () => {
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
        scheduleAt: '2026-06-10T09:00:00',
      },
      savedBody: '## body\n\n- [ ] first\n- [x] second\n',
      editedBody: null,
      scheduleInput: '',
      scheduleValid: false,
      status: 'ready',
      error: null,
    });
    renderWithStore(<TicketDetail />, { store });

    expect(document.querySelector('.mds-ticket .mds-panel')).toBeTruthy();
    expect(screen.getByText('work')).toBeTruthy();
    expect(screen.getByText('t001')).toBeTruthy();
    expect(screen.getByText('split orchestrator')).toBeTruthy();
    expect(screen.getByText('in_progress')).toBeTruthy();
    expect(screen.getByText('2026-06-10T09:00:00')).toBeTruthy();
    expect(document.querySelector('.mds-ticket__schedule .mds-input')).toBeTruthy();
    expect(document.querySelector('.mds-ticket__editor')).toBeTruthy();
    expect(screen.getByText('save')).toBeTruthy();
    expect(screen.getByText('first')).toBeTruthy();
    expect(screen.getByText('second')).toBeTruthy();
    expect(document.querySelectorAll('.mds-ticket__checklist-item').length).toBe(2);
  });

  it('toggles checklist lines in the edited body', () => {
    const { store } = makeStore();
    seedSlice(store, 'ticketDetail', {
      ticketId: 't001',
      frontmatter: {
        title: 't',
        status: 'ready',
        deps: '',
        harness: null,
        model: null,
        worktree: null,
        scheduleAt: null,
      },
      savedBody: '- [ ] item\n',
      editedBody: '- [ ] item\n',
      scheduleInput: '',
      scheduleValid: false,
      status: 'ready',
      error: null,
    });
    renderWithStore(<TicketDetail />, { store });
    const box = document.querySelector('.mds-ticket__checklist-item .mds-check__native');
    expect(box).toBeTruthy();
    fireEvent.click(box!);
    expect(store.getState().ticketDetail.editedBody).toBe('- [x] item\n');
  });

  it('save closes after saveBody + schedule', () => {
    const { store } = makeStore();
    const saveBody = vi.fn(async () => {});
    const schedule = vi.fn(async () => {});
    const close = vi.fn();
    seedSlice(store, 'ticketDetail', {
      ticketId: 't001',
      frontmatter: {
        title: 't',
        status: 'ready',
        deps: '',
        harness: null,
        model: null,
        worktree: null,
        scheduleAt: null,
      },
      savedBody: 'body',
      editedBody: 'body edited',
      scheduleInput: '1h',
      scheduleValid: true,
      status: 'ready',
      error: null,
    });
    store.setState((s) => ({
      actions: {
        ...s.actions,
        ticketDetail: {
          ...s.actions.ticketDetail,
          saveBody,
          schedule,
          close,
        },
      },
    }));
    renderWithStore(<TicketDetail />, { store });
    fireEvent.click(screen.getByRole('button', { name: 'save' }));
    expect(saveBody).toHaveBeenCalled();
    expect(schedule).toHaveBeenCalled();
    expect(close).toHaveBeenCalled();
  });
});
