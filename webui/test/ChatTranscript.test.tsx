/**
 * ChatTranscript (DS reskin) renders the conversation slice as real chat bubbles. We seed
 * `conversations.transcripts[agentId]` with a few block types and assert the speaker → presentation
 * map: user → accent bubble (right), assistant → crow bubble + Avatar, a tool block → struct crow
 * bubble, and a notice → centered meta chip. Empty state shows before any block.
 */

import { cleanup, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { ChatTranscript } from '../src/components/stage/ChatTranscript.js';
import { makeStore, renderWithStore, seedSlice } from './helpers.js';

afterEach(cleanup);

const AID = 'crowzero';

function seedBlocks(store: ReturnType<typeof makeStore>['store'], blocks: unknown[]): void {
  seedSlice(store, 'conversations', {
    transcripts: { [AID]: blocks as never },
    meta: {},
    activePaneAgentId: null,
    paneOverrides: new Map<string, boolean>(),
    clearedFloors: {},
  } as never);
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
    // The crow row carries an Avatar identity tile.
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
});
