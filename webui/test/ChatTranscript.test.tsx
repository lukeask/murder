/**
 * ChatTranscript honors verbose/condensed via paneViewModes + settings.defaultChatViewMode.
 * Condensed folds tool_call runs; Stage toggle writes setPaneViewMode.
 */

import { cleanup, screen } from '@testing-library/react';
import { act } from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { ChatTranscript } from '../src/components/stage/ChatTranscript.js';
import { Stage } from '../src/components/stage/Stage.js';
import { makeStore, renderWithStore, seedSlice } from './helpers.js';

afterEach(cleanup);

const AID = 'crowzero';

function seedBlocks(
  store: ReturnType<typeof makeStore>['store'],
  blocks: unknown[],
  extras?: { paneViewModes?: Record<string, 'verbose' | 'condensed' | 'tmux'>; defaultMode?: 'verbose' | 'condensed' },
): void {
  seedSlice(store, 'conversations', {
    transcripts: { [AID]: blocks as never },
    pendingByAgent: {},
    meta: {},
    activePaneAgentId: AID,
    paneOverrides: new Map<string, boolean>(),
    paneReapAges: new Map<string, number>(),
    clearedFloors: {},
    paneViewModes: extras?.paneViewModes ?? {},
    chunkSummaries: {},
  } as never);
  if (extras?.defaultMode !== undefined) {
    store.setState((s) => ({
      settings: { ...s.settings, defaultChatViewMode: extras.defaultMode! },
    }));
  }
}

describe('ChatTranscript (DS reskin)', () => {
  it('shows the empty state with no blocks', () => {
    const { store } = makeStore();
    renderWithStore(<ChatTranscript agentId={AID} />, { store });
    expect(screen.getByText('No messages yet.')).toBeTruthy();
  });

  it('renders user + assistant turns as opposing chat bubbles', () => {
    const { store } = makeStore();
    seedBlocks(store, [
      { id: '1', type: 'user', raw: { text: 'land the split' } },
      { id: '2', type: 'assistant', raw: { text: 'on it' } },
    ]);
    renderWithStore(<ChatTranscript agentId={AID} />, { store });

    expect(document.querySelector('.mds-bubble--user')).toBeTruthy();
    expect(document.querySelector('.mds-bubble--crow')).toBeTruthy();
    expect(document.querySelector('.mds-msg--crow .mds-avatar')).toBeTruthy();
    expect(screen.getByText('land the split')).toBeTruthy();
    expect(screen.getByText('on it')).toBeTruthy();
  });

  it('renders a notice as a centered muted meta chip', () => {
    const { store } = makeStore();
    seedBlocks(store, [{ id: '1', type: 'notice', raw: { text: 'crow spawned' } }]);
    renderWithStore(<ChatTranscript agentId={AID} />, { store });

    expect(document.querySelector('.mds-msg--meta .mds-meta-chip')).toBeTruthy();
    expect(screen.getByText('crow spawned')).toBeTruthy();
  });

  it('renders pending shadow turns as translucent user bubbles', () => {
    const { store } = makeStore();
    seedSlice(store, 'conversations', {
      transcripts: {},
      pendingByAgent: {
        [AID]: [
          {
            clientId: 'p1',
            agentId: AID,
            text: 'optimistic hello',
            createdAt: 1,
            status: 'sending',
          },
        ],
      },
      meta: {},
      activePaneAgentId: null,
      paneOverrides: new Map<string, boolean>(),
      paneReapAges: new Map<string, number>(),
      clearedFloors: {},
      paneViewModes: {},
      chunkSummaries: {},
    } as never);
    renderWithStore(<ChatTranscript agentId={AID} />, { store });

    expect(document.querySelector('.mds-bubble--delivery-pending')).toBeTruthy();
    expect(screen.getByText('optimistic hello')).toBeTruthy();
    expect(screen.getByText('sending…')).toBeTruthy();
  });

  it('condensed mode folds adjacent tool_call blocks into one activity turn', () => {
    const { store } = makeStore();
    seedBlocks(
      store,
      [
        { id: '1', type: 'user', raw: { text: 'go' } },
        { id: '2', type: 'tool_call', raw: { title: 'read', result: 'a' } },
        { id: '3', type: 'tool_call', raw: { title: 'write', result: 'b' } },
        { id: '4', type: 'assistant', raw: { text: 'done', kind: 'assistant_final' } },
      ],
      { paneViewModes: { [AID]: 'condensed' } },
    );
    renderWithStore(<ChatTranscript agentId={AID} />, { store });

    expect(screen.getByText('go')).toBeTruthy();
    expect(screen.getByText('done')).toBeTruthy();
    // Condensed collapses the tool run — individual tool titles should not both appear as separate bubbles.
    const toolTitles = [...document.querySelectorAll('.mds-bubble')].filter(
      (el) => el.textContent?.includes('read') || el.textContent?.includes('write'),
    );
    expect(toolTitles.length).toBeLessThan(2);
  });

  it('honors settings.defaultChatViewMode when pane has no override', () => {
    const { store } = makeStore();
    seedBlocks(
      store,
      [
        { id: '1', type: 'tool_call', raw: { title: 'alpha', result: '1' } },
        { id: '2', type: 'tool_call', raw: { title: 'beta', result: '2' } },
      ],
      { defaultMode: 'condensed' },
    );
    renderWithStore(<ChatTranscript agentId={AID} />, { store });
    const bubbles = document.querySelectorAll('.mds-bubble, .mds-meta-chip');
    // One collapsed activity line rather than two tool bubbles.
    expect(bubbles.length).toBeLessThanOrEqual(1);
  });
});

describe('Stage view mode toggle', () => {
  it('writes paneViewModes via the Verbose/Condensed control', () => {
    const { store } = makeStore();
    seedSlice(store, 'conversations', {
      transcripts: { [AID]: [] },
      pendingByAgent: {},
      meta: {},
      activePaneAgentId: AID,
      paneOverrides: new Map([[AID, true]]),
      paneReapAges: new Map<string, number>(),
      clearedFloors: {},
      paneViewModes: {},
      chunkSummaries: {},
    } as never);
    seedSlice(store, 'roster', {
      status: 'ready',
      error: null,
      rows: [
        {
          agentId: AID,
          sessionId: '0198b156-2dd3-70a9-bc79-fca001dc8801',
          role: 'collaborator',
          status: 'running',
          harness: 'claude',
          model: 'x',
          ticketId: null,
          ticketTitle: null,
          session: null,
        },
      ],
    } as never);

    renderWithStore(<Stage />, { store });
    const condensed = screen.getByRole('tab', { name: 'Condensed' });
    act(() => {
      condensed.click();
    });
    expect(store.getState().conversations.paneViewModes[AID]).toBe('condensed');

    const verbose = screen.getByRole('tab', { name: 'Verbose' });
    act(() => {
      verbose.click();
    });
    expect(store.getState().conversations.paneViewModes[AID]).toBe('verbose');
  });
});
