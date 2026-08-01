/**
 * ChatInput — submit pipeline, history recall, vim toggle smoke, queued chrome.
 */

import { cleanup, fireEvent, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ChatInput } from '../src/components/stage/ChatInput.js';
import { createComposerStores } from '../src/composer/createComposerStores.js';
import { makeStore, renderWithStore, seedSlice } from './helpers.js';

afterEach(cleanup);

const AID = 'collab-1';

function seedActiveAgent(store: ReturnType<typeof makeStore>['store']): void {
  seedSlice(store, 'roster', {
    rows: [
      {
        agentId: AID,
        role: 'collaborator',
        ticketId: null,
        ticketTitle: null,
        harness: 'claude',
        model: 'opus',
        status: 'idle',
        session: 's1',
      },
    ],
    status: 'ready',
    error: null,
  });
  seedSlice(store, 'conversations', {
    transcripts: {},
    pendingByAgent: {},
    meta: {},
    activePaneAgentId: AID,
    paneOverrides: new Map(),
    paneReapAges: new Map(),
    clearedFloors: {},
    paneViewModes: {},
    chunkSummaries: {},
  } as never);
}

describe('ChatInput submit pipeline', () => {
  it('plain Enter sends via conversations.send and clears the composer', () => {
    const { store } = makeStore();
    seedActiveAgent(store);
    const send = vi.fn(async () => {});
    store.setState({
      actions: {
        ...store.getState().actions,
        conversations: { ...store.getState().actions.conversations, send },
      },
    });
    renderWithStore(<ChatInput />, { store });

    const field = screen.getByPlaceholderText(`message ${AID}…`);
    fireEvent.change(field, { target: { value: 'hello there' } });
    fireEvent.keyDown(field, { key: 'Enter' });

    expect(send).toHaveBeenCalledWith(AID, 'hello there');
    expect((field as HTMLTextAreaElement).value).toBe('');
  });

  it('Shift+Enter does not submit', () => {
    const { store } = makeStore();
    seedActiveAgent(store);
    const send = vi.fn(async () => {});
    store.setState({
      actions: {
        ...store.getState().actions,
        conversations: { ...store.getState().actions.conversations, send },
      },
    });
    renderWithStore(<ChatInput />, { store });

    const field = screen.getByPlaceholderText(`message ${AID}…`);
    fireEvent.change(field, { target: { value: 'line' } });
    fireEvent.keyDown(field, { key: 'Enter', shiftKey: true });

    expect(send).not.toHaveBeenCalled();
    expect((field as HTMLTextAreaElement).value).toBe('line');
  });

  it(':workflow fires workflows.run instead of send', () => {
    const { store } = makeStore();
    seedActiveAgent(store);
    seedSlice(store, 'workflows', {
      items: [
        {
          name: 'deploy',
          description: '',
          mode: 'serial',
          stages: [],
        },
      ],
      status: 'ready',
      error: null,
      revision: '1',
    } as never);
    const send = vi.fn(async () => {});
    const run = vi.fn(async () => {});
    store.setState({
      actions: {
        ...store.getState().actions,
        conversations: { ...store.getState().actions.conversations, send },
        workflows: { ...store.getState().actions.workflows, run },
      },
    });
    renderWithStore(<ChatInput />, { store });

    const field = screen.getByPlaceholderText(`message ${AID}…`);
    fireEvent.change(field, { target: { value: ':deploy ship it' } });
    fireEvent.keyDown(field, { key: 'Enter' });

    expect(run).toHaveBeenCalledWith('deploy', { input: 'ship it' });
    expect(send).not.toHaveBeenCalled();
  });

  it(':help routes through dispatchCommand (no send)', () => {
    const { store } = makeStore();
    seedActiveAgent(store);
    const send = vi.fn(async () => {});
    store.setState({
      actions: {
        ...store.getState().actions,
        conversations: { ...store.getState().actions.conversations, send },
      },
    });
    renderWithStore(<ChatInput />, { store });

    const field = screen.getByPlaceholderText(`message ${AID}…`);
    fireEvent.change(field, { target: { value: ':help' } });
    fireEvent.keyDown(field, { key: 'Enter' });

    expect(send).not.toHaveBeenCalled();
    expect((field as HTMLTextAreaElement).value).toBe('');
  });

  it('/compact passthrough uses sendKey, not send', () => {
    const { store } = makeStore();
    seedActiveAgent(store);
    const send = vi.fn(async () => {});
    const sendKey = vi.fn(async () => {});
    store.setState({
      actions: {
        ...store.getState().actions,
        conversations: { ...store.getState().actions.conversations, send, sendKey },
      },
    });
    renderWithStore(<ChatInput />, { store });

    const field = screen.getByPlaceholderText(`message ${AID}…`);
    fireEvent.change(field, { target: { value: '/compact' } });
    fireEvent.keyDown(field, { key: 'Enter' });

    expect(sendKey).toHaveBeenCalledWith(AID, '/compact', true, true);
    expect(send).not.toHaveBeenCalled();
  });

  it('empty Enter interrupts the active agent', () => {
    const { store } = makeStore();
    seedActiveAgent(store);
    const interrupt = vi.fn(async () => {});
    const send = vi.fn(async () => {});
    store.setState({
      actions: {
        ...store.getState().actions,
        conversations: { ...store.getState().actions.conversations, send, interrupt },
      },
    });
    renderWithStore(<ChatInput />, { store });

    const field = screen.getByPlaceholderText(`message ${AID}…`);
    fireEvent.keyDown(field, { key: 'Enter' });

    expect(interrupt).toHaveBeenCalledWith(AID);
    expect(send).not.toHaveBeenCalled();
  });
});

describe('ChatInput history recall', () => {
  it('ArrowUp recalls a prior send; ArrowDown restores the draft', () => {
    const { store } = makeStore();
    seedActiveAgent(store);
    const composer = createComposerStores();
    composer.chatHistory.getState().record('first send');
    composer.chatHistory.getState().record('second send');
    renderWithStore(<ChatInput />, { store, composer });

    const field = screen.getByPlaceholderText(`message ${AID}…`) as HTMLTextAreaElement;
    fireEvent.change(field, { target: { value: 'live draft' } });
    // Cursor at buffer start = top visual row even when jsdom reports clientWidth 0 (width≈1).
    composer.chatInput.getState().setBuffer({
      text: 'live draft',
      cursor: 0,
      desiredVisualColumn: null,
    });

    fireEvent.keyDown(field, { key: 'ArrowUp' });
    expect(composer.chatInput.getState().text).toBe('second send');

    fireEvent.keyDown(field, { key: 'ArrowUp' });
    expect(composer.chatInput.getState().text).toBe('first send');

    // History loads park the cursor at the entry end (bottom visual edge) → Down walks newer.
    fireEvent.keyDown(field, { key: 'ArrowDown' });
    expect(composer.chatInput.getState().text).toBe('second send');

    fireEvent.keyDown(field, { key: 'ArrowDown' });
    expect(composer.chatInput.getState().text).toBe('live draft');
  });

  it('successful send records into the history ring', () => {
    const { store } = makeStore();
    seedActiveAgent(store);
    const composer = createComposerStores();
    const send = vi.fn(async () => {});
    store.setState({
      actions: {
        ...store.getState().actions,
        conversations: { ...store.getState().actions.conversations, send },
      },
    });
    renderWithStore(<ChatInput />, { store, composer });

    const field = screen.getByPlaceholderText(`message ${AID}…`);
    fireEvent.change(field, { target: { value: 'remember me' } });
    fireEvent.keyDown(field, { key: 'Enter' });

    expect(composer.chatHistory.getState().entries).toEqual(['remember me']);
  });
});

describe('ChatInput vim mode', () => {
  it('Esc → NORMAL and i → INSERT when settings.vimMode is on', () => {
    const { store } = makeStore();
    seedActiveAgent(store);
    seedSlice(store, 'settings', {
      ...store.getState().settings,
      vimMode: true,
    });
    const composer = createComposerStores();
    // Store starts in NORMAL; enter insert then Esc back.
    composer.chatVim.getState().setSubmode('insert');
    renderWithStore(<ChatInput />, { store, composer });

    expect(screen.getByText(/INSERT/)).toBeTruthy();

    const field = screen.getByPlaceholderText(`message ${AID}…`);
    fireEvent.keyDown(field, { key: 'Escape' });
    expect(composer.chatVim.getState().submode).toBe('normal');
    expect(screen.getByText(/NORMAL/)).toBeTruthy();

    fireEvent.keyDown(field, { key: 'i' });
    expect(composer.chatVim.getState().submode).toBe('insert');
  });
});

describe('ChatInput queued chrome', () => {
  it('shows the queued indicator when meta.queuedMessage is set', () => {
    const { store } = makeStore();
    seedActiveAgent(store);
    seedSlice(store, 'conversations', {
      ...store.getState().conversations,
      meta: {
        [AID]: { liveState: 'working', queuedMessage: 'please also check the tests' },
      },
    } as never);
    renderWithStore(<ChatInput />, { store });

    expect(screen.getByText('queued')).toBeTruthy();
    expect(screen.getByText(/please also check the tests/)).toBeTruthy();
    expect(screen.getByText(/interrupt & send now/)).toBeTruthy();
  });
});

describe('ChatInput choice UI', () => {
  it('renders descriptions and checkbox marks for multi-select options', () => {
    const { store } = makeStore();
    seedActiveAgent(store);
    seedSlice(store, 'conversations', {
      ...store.getState().conversations,
      transcripts: {
        [AID]: [
          {
            id: '1',
            type: 'choice_prompt',
            raw: {
              question: 'Pick features',
              answered: false,
              multi: true,
              selected: 1,
              options: [
                {
                  number: 1,
                  label: 'Tests',
                  description: 'run the suite',
                  checked: true,
                },
                {
                  number: 2,
                  label: 'Lint',
                  description: 'biome check',
                  checked: false,
                },
                { number: 3, label: 'Type something', description: null, checked: null },
              ],
            },
          },
        ],
      },
    } as never);
    renderWithStore(<ChatInput />, { store });

    expect(screen.getByText('Pick features')).toBeTruthy();
    expect(screen.getByText('run the suite')).toBeTruthy();
    expect(screen.getByText('biome check')).toBeTruthy();
    expect(screen.getByText('Submit')).toBeTruthy();
    const checks = document.querySelectorAll('.mds-composer__opt-check');
    expect(checks.length).toBe(2);
    expect(checks[0]?.getAttribute('data-checked')).toBe('true');
    expect(checks[1]?.getAttribute('data-checked')).toBe('false');
  });
});
