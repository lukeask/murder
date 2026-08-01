/**
 * Stage multi-pane: two open transcripts side-by-side; close pane removes it from the grid.
 */

import { cleanup, fireEvent, screen } from '@testing-library/react';
import { act } from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { Stage } from '../src/components/stage/Stage.js';
import { makeStore, renderWithStore, seedSlice } from './helpers.js';
import type { RosterRow } from '@murder/ui-core/store/roster/rosterSlice.js';

afterEach(cleanup);

const AID_A = 'crow-a';
const AID_B = 'crow-b';

function rosterRow(agentId: string, role: RosterRow['role'] = 'collaborator'): RosterRow {
  return {
    agentId,
    sessionId: `0198b156-2dd3-70a9-bc79-${agentId.padEnd(12, '0').slice(0, 12)}`,
    role,
    status: 'running',
    harness: 'claude',
    model: 'x',
    ticketId: null,
    ticketTitle: null,
    session: `session-${agentId}`,
  };
}

function seedTwoOpenPanes(store: ReturnType<typeof makeStore>['store']): void {
  seedSlice(store, 'conversations', {
    transcripts: {
      [AID_A]: [{ id: '1', type: 'user', raw: { text: 'hello-a' } }] as never,
      [AID_B]: [{ id: '2', type: 'user', raw: { text: 'hello-b' } }] as never,
    },
    pendingByAgent: {},
    meta: {},
    activePaneAgentId: AID_A,
    paneOverrides: new Map([
      [AID_A, true],
      [AID_B, true],
    ]),
    paneReapAges: new Map<string, number>(),
    clearedFloors: {},
    paneViewModes: {},
    chunkSummaries: {},
  } as never);
  seedSlice(store, 'roster', {
    status: 'ready',
    error: null,
    rows: [rosterRow(AID_A), rosterRow(AID_B, 'crow')],
  } as never);
}

describe('Stage multi-pane', () => {
  it('renders two open transcript panes side by side', () => {
    const { store } = makeStore();
    seedTwoOpenPanes(store);
    renderWithStore(<Stage />, { store });

    const panes = document.querySelectorAll('.mds-stage-pane');
    expect(panes.length).toBe(2);
    expect(document.querySelector(`[data-agent-id="${AID_A}"]`)).toBeTruthy();
    expect(document.querySelector(`[data-agent-id="${AID_B}"]`)).toBeTruthy();
    expect(screen.getByText('hello-a')).toBeTruthy();
    expect(screen.getByText('hello-b')).toBeTruthy();

    const grid = document.querySelector('.mds-stage__transcripts');
    expect(grid?.getAttribute('style') ?? '').toMatch(/--stage-cols:\s*2/);
    expect(document.querySelector(`[data-agent-id="${AID_A}"]`)?.getAttribute('data-focused')).toBe(
      'true',
    );
  });

  it('closes a pane via the close button and leaves the other open', () => {
    const { store } = makeStore();
    seedTwoOpenPanes(store);
    renderWithStore(<Stage />, { store });

    expect(document.querySelectorAll('.mds-stage-pane').length).toBe(2);

    act(() => {
      fireEvent.click(screen.getByLabelText(`Close pane ${AID_B}`));
    });

    expect(store.getState().conversations.paneOverrides.get(AID_B)).toBe(false);
    expect(document.querySelectorAll('.mds-stage-pane').length).toBe(1);
    expect(document.querySelector(`[data-agent-id="${AID_A}"]`)).toBeTruthy();
    expect(document.querySelector(`[data-agent-id="${AID_B}"]`)).toBeNull();
    expect(screen.getByText('hello-a')).toBeTruthy();
    expect(screen.queryByText('hello-b')).toBeNull();
  });

  it('tiles a doc beside transcripts instead of full takeover', () => {
    const { store } = makeStore();
    seedTwoOpenPanes(store);
    seedSlice(store, 'docView', {
      open: { kind: 'plan', name: 'plan.md' },
      status: 'ready',
      body: '# Plan body',
      error: null,
    } as never);
    renderWithStore(<Stage />, { store });

    expect(document.querySelector('.mds-stage__doc-col')).toBeTruthy();
    expect(document.querySelectorAll('.mds-stage-pane').length).toBe(2);
    expect(screen.getByText('hello-a')).toBeTruthy();
    // Not the old full-stage overlay takeover class on the root.
    expect(document.querySelector('.mds-stage--overlay')).toBeNull();
  });

  it('writes paneViewModes via the per-pane Verbose/Condensed control', () => {
    const { store } = makeStore();
    seedTwoOpenPanes(store);
    // Close B so only one view-mode control is present (matches prior Stage test).
    store.getState().actions.conversations.setTranscriptPaneOpen(AID_B, false);
    renderWithStore(<Stage />, { store });

    const condensed = screen.getByRole('tab', { name: 'Condensed' });
    act(() => {
      condensed.click();
    });
    expect(store.getState().conversations.paneViewModes[AID_A]).toBe('condensed');
  });
});
