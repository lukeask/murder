/**
 * MurderConfirmDialog + RosterPanel murder arm → agent.stop on confirm.
 */

import { murderConfirmStore } from '@murder/ui-core/store/murder/murderConfirmStore.js';
import type { RosterRow } from '@murder/ui-core/store/roster/rosterSlice.js';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MurderConfirmDialog } from '../src/components/modals/MurderConfirmDialog.js';
import { RosterPanel } from '../src/components/panels/RosterPanel.js';
import { makeStore, renderWithStore, seedSlice } from './helpers.js';

afterEach(() => {
  cleanup();
  murderConfirmStore.getState().clear();
});

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

describe('MurderConfirmDialog', () => {
  it('roster murder button arms the confirm store and dialog confirms via agent.stop', async () => {
    const { store, bus } = makeStore();
    const stop = vi.fn(() => ({ ok: true }));
    bus.stubCommand('agent.stop', stop);

    seedSlice(store, 'roster', {
      rows: [row({ agentId: 'a1', role: 'crow', ticketId: null })],
      status: 'ready',
      error: null,
    });

    renderWithStore(
      <>
        <RosterPanel />
        <MurderConfirmDialog />
      </>,
      { store, bus },
    );

    fireEvent.click(screen.getByLabelText('Murder a1'));
    expect(murderConfirmStore.getState().pending?.agentId).toBe('a1');
    expect(screen.getByRole('dialog')).toBeTruthy();

    fireEvent.click(document.querySelector('.mds-btn--brand')!);
    await waitFor(() => {
      expect(stop).toHaveBeenCalled();
    });
    expect(murderConfirmStore.getState().pending).toBeNull();
  });

  it('cancel clears pending without stopping', () => {
    const { store, bus } = makeStore();
    const stop = vi.fn(() => ({ ok: true }));
    bus.stubCommand('agent.stop', stop);

    murderConfirmStore.getState().arm({ agentId: 'x', name: 'X' });
    renderWithStore(<MurderConfirmDialog />, { store, bus });

    fireEvent.click(screen.getByRole('button', { name: /Cancel/i }));
    expect(murderConfirmStore.getState().pending).toBeNull();
    expect(stop).not.toHaveBeenCalled();
  });
});
