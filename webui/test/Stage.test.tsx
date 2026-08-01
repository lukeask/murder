/**
 * Stage multi-pane: two open transcripts side-by-side; close pane removes it from the grid.
 */

import { cleanup, fireEvent, screen } from '@testing-library/react';
import { act } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Stage, shouldDualSideColumns } from '../src/components/stage/Stage.js';
import { makeStore, renderWithStore, seedSlice } from './helpers.js';
import type { RosterRow } from '@murder/ui-core/store/roster/rosterSlice.js';
import { MOBILE_QUERY } from '../src/useMediaQuery.js';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function stubMatchMedia(isMobile: boolean): void {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query === MOBILE_QUERY ? isMobile : false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    onchange: null,
    dispatchEvent: () => false,
  }));
}


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
  it('shouldDualSideColumns is true only when both sides open and not narrow', () => {
    expect(shouldDualSideColumns(true, false)).toBe(true);
    expect(shouldDualSideColumns(true, true)).toBe(false);
    expect(shouldDualSideColumns(false, false)).toBe(false);
  });

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

  it('tiles an open ticket beside transcripts (not full takeover)', () => {
    const { store } = makeStore();
    seedTwoOpenPanes(store);
    seedSlice(store, 'ticketDetail', {
      ticketId: 't001',
      frontmatter: {
        title: 'split',
        status: 'ready',
        deps: '',
        harness: null,
        model: null,
        worktree: null,
        scheduleAt: null,
      },
      savedBody: 'body',
      editedBody: null,
      scheduleInput: '',
      scheduleValid: false,
      status: 'ready',
      error: null,
    });
    renderWithStore(<Stage />, { store });

    expect(document.querySelector('.mds-stage__doc-col--ticket')).toBeTruthy();
    expect(document.querySelector('.mds-ticket')).toBeTruthy();
    expect(document.querySelectorAll('.mds-stage-pane').length).toBe(2);
    expect(document.querySelector('.mds-stage--overlay')).toBeNull();
    expect(screen.getByLabelText('Expand ticket to full stage')).toBeTruthy();
  });

  it('shows doc and ticket as dual side columns when both are open', () => {
    stubMatchMedia(false);
    const { store } = makeStore();
    seedTwoOpenPanes(store);
    seedSlice(store, 'docView', {
      open: { kind: 'plan', name: 'plan.md' },
      status: 'ready',
      body: '# Plan body',
      error: null,
    } as never);
    seedSlice(store, 'ticketDetail', {
      ticketId: 't001',
      frontmatter: {
        title: 'split',
        status: 'ready',
        deps: '',
        harness: null,
        model: null,
        worktree: null,
        scheduleAt: null,
      },
      savedBody: 'body',
      editedBody: null,
      scheduleInput: '',
      scheduleValid: false,
      status: 'ready',
      error: null,
    });
    renderWithStore(<Stage />, { store });

    expect(document.querySelector('.mds-stage__side--dual')).toBeTruthy();
    expect(document.querySelector('.mds-stage__doc-col')).toBeTruthy();
    expect(document.querySelector('.mds-stage__doc-col--ticket')).toBeTruthy();
    expect(document.querySelectorAll('.mds-stage-pane').length).toBe(2);
    expect(document.querySelector('.mds-stage__grid')?.getAttribute('data-dual-side')).toBe(
      'true',
    );
    // Transcript tiling still uses side-column weights (hasDoc=true).
    expect(document.querySelector('.mds-stage__transcripts')?.getAttribute('style') ?? '').toMatch(
      /--stage-cols:\s*1/,
    );
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

  it('honors paneViewModes tmux as an in-pane terminal surface', () => {
    const { store } = makeStore();
    seedTwoOpenPanes(store);
    store.getState().actions.conversations.setTranscriptPaneOpen(AID_B, false);
    store.getState().actions.conversations.setPaneViewMode(AID_A, 'tmux');
    renderWithStore(<Stage />, { store });

    const pane = document.querySelector(`[data-agent-id="${AID_A}"]`);
    expect(pane?.getAttribute('data-view-mode')).toBe('tmux');
    expect(document.querySelector('.mds-tmux, .mds-tmux__empty')).toBeTruthy();
    expect(screen.queryByText('hello-a')).toBeNull();

    const tmuxTab = screen.getByRole('tab', { name: 'Tmux' });
    expect(tmuxTab.getAttribute('aria-selected')).toBe('true');
  });

  it('cycles verbose → condensed → tmux via setPaneViewMode cycle action', () => {
    const { store } = makeStore();
    seedTwoOpenPanes(store);
    store.getState().actions.conversations.setTranscriptPaneOpen(AID_B, false);
    expect(store.getState().conversations.paneViewModes[AID_A]).toBeUndefined();

    act(() => {
      store.getState().actions.conversations.cyclePaneViewMode(AID_A);
    });
    expect(store.getState().conversations.paneViewModes[AID_A]).toBe('condensed');
    act(() => {
      store.getState().actions.conversations.cyclePaneViewMode(AID_A);
    });
    expect(store.getState().conversations.paneViewModes[AID_A]).toBe('tmux');
    act(() => {
      store.getState().actions.conversations.cyclePaneViewMode(AID_A);
    });
    expect(store.getState().conversations.paneViewModes[AID_A]).toBe('verbose');
  });
});
