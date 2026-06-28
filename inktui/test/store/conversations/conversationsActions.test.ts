/**
 * Conversations actions tests — C10 delivery gate.
 *
 * Covers the four hard-gate requirements:
 *  1. Conversations slice fed by an emitted `ConversationBlockEvent` updates the right agent's
 *     history; only that data ref-swaps (granularity contract).
 *  2. `agent.message` action is the sole bus caller and sends to the routed `agent_id`.
 *  3. `applyBlock` handles `block-appended` (push) and `block-updated` (replace trailing match).
 *  4. `setActivePaneAgentId` updates the active pane; `send` auto-sets it after success.
 *  5. Unrelated events don't touch the conversations slice.
 *
 * Pattern: FakeBusClient + createAppStore (the C3/C8 store-test idiom).
 */

import { beforeEach, describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../../src/bus/FakeBusClient.js';
import type { ConversationBlockEvent } from '../../../src/bus/protocol.js';
import { initialConversationsState } from '../../../src/store/conversations/conversationsSlice.js';
import { createAppStore, initialAppState } from '../../../src/store/store.js';
import { toastStore } from '../../../src/store/toast/toastStore.js';

// ── Helpers ──────────────────────────────────────────────────────────────────────────────────────

function setup() {
  const fake = new FakeBusClient();
  // Stub sibling RPCs so the store doesn't reject.
  fake.stubRpc('state.crow_snapshot', { invalidation_key: 'iv', sessions: [] });
  // F2: `agent.message` is an orchestrator command kind routed through command.submit + status.
  fake.stubRpc('command.submit', { ok: true, command_id: 'cmd-1' });
  fake.stubRpc('command.status', { ok: true, status: 'done', result_json: '{}' });
  const { store, dispose } = createAppStore(fake);
  return { fake, store, dispose };
}

/**
 * Build a minimal `ConversationBlockEvent` for a given agent.
 *
 * NOTE: this uses a SIMPLIFIED flat `{type,id,text}` block to keep the store-mechanics tests
 * (push/replace/ref-swap) readable. `parseBlock` accepts it via its defensive fallback (no
 * `payload` → treat the row as the segment). The REAL storage-row wire shape (nested `payload`,
 * numeric `id`, `kind` discriminant) and its rendering are pinned by the cross-language golden
 * contract test — see `conversationBlockContract.test.ts`. These two files are complementary:
 * this one tests slice mechanics, that one tests the Python⇆Ink shape contract.
 */
function makeBlockEvent(
  agentId: string,
  blockType: string,
  action: 'block-appended' | 'block-updated',
  extras: Record<string, unknown> = {},
): ConversationBlockEvent {
  return {
    type: 'conversation.block',
    id: `ev-${agentId}-${blockType}`,
    ts: '2026-06-08T00:00:00Z',
    run_id: 'run-1',
    agent_id: agentId,
    conversation_id: `conv-${agentId}`, // present on the wire but NOT used for routing
    action,
    block: { type: blockType, id: `block-${agentId}-${blockType}`, ...extras },
  };
}

// ── Boot state ────────────────────────────────────────────────────────────────────────────────────

describe('conversations — initial state', () => {
  it('starts empty', () => {
    const { store, dispose } = setup();
    expect(store.getState().conversations).toEqual(initialConversationsState);
    dispose();
  });

  it('initialAppState mirrors the slice initial state', () => {
    expect(initialAppState.conversations).toEqual(initialConversationsState);
  });
});

// ── applyBlock via bus.subscribe (the event-driven path) ─────────────────────────────────────────

describe('conversations — event-driven: ConversationBlockEvent via subscribe', () => {
  it('block-appended: pushes a block onto the agent transcript', () => {
    const { fake, store, dispose } = setup();

    // Emit via FakeBusClient — routes through the store's second bus.subscribe.
    const event = makeBlockEvent('agent-1', 'user', 'block-appended', { text: 'hello' });
    fake.emit(event);

    const transcripts = store.getState().conversations.transcripts;
    const blocks = transcripts['agent-1'];
    expect(blocks).toBeDefined();
    expect(blocks).toHaveLength(1);
    expect(blocks?.[0]?.type).toBe('user');
    dispose();
  });

  it('two events for the same agent push two blocks', () => {
    const { fake, store, dispose } = setup();

    fake.emit(makeBlockEvent('agent-1', 'user', 'block-appended', { text: 'msg 1' }));
    fake.emit(makeBlockEvent('agent-1', 'assistant', 'block-appended', { text: 'reply 1' }));

    const blocks = store.getState().conversations.transcripts['agent-1'];
    expect(blocks).toHaveLength(2);
    expect(blocks?.[0]?.type).toBe('user');
    expect(blocks?.[1]?.type).toBe('assistant');
    dispose();
  });

  it('only the affected agent transcript ref-swaps (granularity contract)', () => {
    const { fake, store, dispose } = setup();

    // Seed agent-1 with one block.
    fake.emit(makeBlockEvent('agent-1', 'user', 'block-appended', { text: 'init' }));
    const transcriptsBefore = store.getState().conversations.transcripts;
    const agent1Before = transcriptsBefore['agent-1'];
    const agent2Before = transcriptsBefore['agent-2']; // undefined — no blocks yet

    // Now add a block for agent-2.
    fake.emit(makeBlockEvent('agent-2', 'assistant', 'block-appended', { text: 'hi' }));
    const transcriptsAfter = store.getState().conversations.transcripts;

    // agent-2 changed.
    expect(transcriptsAfter['agent-2']).toHaveLength(1);
    // agent-1's array ref is unchanged — identity preserved.
    expect(transcriptsAfter['agent-1']).toBe(agent1Before);
    expect(agent2Before).toBeUndefined();
    dispose();
  });

  it('block-updated: replaces the last block with matching id, or pushes if no match', () => {
    const { fake, store, dispose } = setup();

    const blockId = 'block-agent-1-assistant';
    // First append a block with a known id.
    fake.emit(makeBlockEvent('agent-1', 'assistant', 'block-appended', { text: 'draft' }));
    const after1 = store.getState().conversations.transcripts['agent-1'];
    expect(after1).toHaveLength(1);

    // Now update it — same id, new content.
    fake.emit(makeBlockEvent('agent-1', 'assistant', 'block-updated', { text: 'final' }));
    const after2 = store.getState().conversations.transcripts['agent-1'];
    expect(after2).toHaveLength(1); // still one block (replaced, not appended)
    // biome-ignore lint/complexity/useLiteralKeys: raw is Record<string,unknown>; noPropertyAccessFromIndexSignature requires bracket notation here in tests too
    expect(after2?.[0]?.raw['text']).toBe('final');

    void blockId;
    dispose();
  });

  it('block-updated with no matching id falls back to push (defensive)', () => {
    const { fake, store, dispose } = setup();

    // block-updated on an empty transcript — no match → push.
    fake.emit({
      type: 'conversation.block',
      id: 'ev-x',
      ts: '2026-06-08T00:00:00Z',
      run_id: 'run-1',
      agent_id: 'agent-x',
      conversation_id: 'conv-x',
      action: 'block-updated',
      block: { type: 'assistant', id: 'no-match', text: 'pushed anyway' },
    });

    const blocks = store.getState().conversations.transcripts['agent-x'];
    expect(blocks).toHaveLength(1);
    expect(blocks?.[0]?.type).toBe('assistant');
    dispose();
  });

  it('state.snapshot events for unrelated entities do not affect conversations', () => {
    const { fake, store, dispose } = setup();

    // Emit a state.snapshot for 'agent' — this should trigger roster refresh, not conversations.
    fake.emit({
      type: 'state.snapshot',
      id: 'ev-ss',
      ts: '2026-06-08T00:00:00Z',
      run_id: 'run-1',
      agent_id: 'agent-1',
      entity: 'agent',
      key: 'k',
      entity_version: 1,
    });

    expect(store.getState().conversations.transcripts).toEqual({});
    dispose();
  });
});

// ── send action (the sole bus caller for chat) ────────────────────────────────────────────────────

describe('conversationsActions.send', () => {
  it('submits an agent.message command with the correct agent_id + message', async () => {
    const { fake, store, dispose } = setup();

    await store.getState().actions.conversations.send('agent-42', 'test message');

    // The message rides the `command.submit` choke point as the `agent.message` command kind.
    const submit = fake.rpcCalls.find((c) => c.method === 'command.submit');
    expect(submit?.params).toMatchObject({
      kind: 'agent.message',
      payload: { agent_id: 'agent-42', message: 'test message' },
    });
    dispose();
  });

  it('sets activePaneAgentId after a successful send', async () => {
    const { store, dispose } = setup();

    await store.getState().actions.conversations.send('agent-99', 'hello');

    expect(store.getState().conversations.activePaneAgentId).toBe('agent-99');
    dispose();
  });

  it('swallows bus errors (fire-and-forget from UI perspective)', async () => {
    const fake = new FakeBusClient();
    // Leave `command.submit` unstubbed — FakeBusClient rejects unknown methods, so the underlying
    // agent.message command rejects; `send` must swallow it. state.crow_snapshot lets store init pass.
    fake.stubRpc('state.crow_snapshot', { invalidation_key: 'iv', sessions: [] });
    const { store, dispose } = createAppStore(fake);

    // Should not throw — send swallows the rejection.
    await expect(
      store.getState().actions.conversations.send('agent-1', 'hi'),
    ).resolves.toBeUndefined();
    dispose();
  });

  it('is the SOLE caller for chat — exactly one agent.message command is submitted', async () => {
    // This test proves the rule-3 invariant: only conversationsActions.send sends chat. We verify
    // by checking exactly one `agent.message`-kind command.submit appears after a send.
    const { fake, store, dispose } = setup();

    await store.getState().actions.conversations.send('agent-1', 'msg');
    const agentMessageCalls = fake.rpcCalls.filter(
      (c) =>
        c.method === 'command.submit' && (c.params as { kind: string }).kind === 'agent.message',
    );
    expect(agentMessageCalls).toHaveLength(1);
    dispose();
  });
});

// ── F9: send pushes a toast on bus ack (TODO-T) ─────────────────────────────────────────────────

describe('conversationsActions.send — toast on bus ack (F9)', () => {
  /** Build a store whose `agent.message` command resolves with a given `result_json` body, so the
   * send action sees the branch under test (success / queued / handled-false). */
  function setupWithResult(resultJson: string) {
    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', { invalidation_key: 'iv', sessions: [] });
    fake.stubRpc('command.submit', { ok: true, command_id: 'cmd-1' });
    fake.stubRpc('command.status', { ok: true, status: 'done', result_json: resultJson });
    const { store, dispose } = createAppStore(fake);
    return { store, dispose };
  }

  beforeEach(() => {
    // The send action pushes to the toastStore singleton — reset it between cases.
    toastStore.getState().clear();
  });

  it('pushes "→ {agentId}" (info) on a successful ack — not at keypress, on the ack', async () => {
    const { store, dispose } = setupWithResult('{}');
    expect(toastStore.getState().toasts).toHaveLength(0); // nothing before the ack

    await store.getState().actions.conversations.send('crow-7', 'hi');

    const toasts = toastStore.getState().toasts;
    expect(toasts).toHaveLength(1);
    expect(toasts[0]?.text).toBe('→ crow-7');
    expect(toasts[0]?.severity).toBe('info');
    toastStore.getState().clear();
    dispose();
  });

  it('pushes "message queued (crow busy)" when the ack reports queued', async () => {
    const { store, dispose } = setupWithResult(JSON.stringify({ queued: true }));

    await store.getState().actions.conversations.send('crow-7', 'hi');

    const toasts = toastStore.getState().toasts;
    expect(toasts).toHaveLength(1);
    expect(toasts[0]?.text).toBe('message queued (crow busy)');
    expect(toasts[0]?.severity).toBe('info');
    toastStore.getState().clear();
    dispose();
  });

  it('pushes an error toast (and skips the → toast + pane-set) when handled === false', async () => {
    const { store, dispose } = setupWithResult(
      JSON.stringify({ handled: false, error: 'agent did not handle message' }),
    );

    await store.getState().actions.conversations.send('crow-7', 'hi');

    const toasts = toastStore.getState().toasts;
    expect(toasts).toHaveLength(1);
    expect(toasts[0]?.text).toBe('agent did not handle message');
    expect(toasts[0]?.severity).toBe('error');
    // Faithful to Textual: a rejected message does not become the active pane.
    expect(store.getState().conversations.activePaneAgentId).toBeNull();
    toastStore.getState().clear();
    dispose();
  });
});

// ── setActivePaneAgentId ──────────────────────────────────────────────────────────────────────────

describe('conversationsActions.setActivePaneAgentId', () => {
  it('sets and clears the active pane', () => {
    const { store, dispose } = setup();

    store.getState().actions.conversations.setActivePaneAgentId('agent-3');
    expect(store.getState().conversations.activePaneAgentId).toBe('agent-3');

    store.getState().actions.conversations.setActivePaneAgentId(null);
    expect(store.getState().conversations.activePaneAgentId).toBeNull();
    dispose();
  });
});

// ── pane open/close model (item 9b/9c) ───────────────────────────────────────────────────────────

describe('conversationsActions — chat-pane overrides', () => {
  it('setChatPaneOpen writes an override entry', () => {
    const { store, dispose } = setup();
    store.getState().actions.conversations.setChatPaneOpen('agent-7', true);
    expect(store.getState().conversations.paneOverrides.get('agent-7')).toBe(true);
    store.getState().actions.conversations.setChatPaneOpen('agent-7', false);
    expect(store.getState().conversations.paneOverrides.get('agent-7')).toBe(false);
    dispose();
  });

  it('toggleChatPane records the flip of the passed currentlyOpen state', () => {
    const { store, dispose } = setup();
    // Currently open → toggle records false (close).
    store.getState().actions.conversations.toggleChatPane('agent-9', true);
    expect(store.getState().conversations.paneOverrides.get('agent-9')).toBe(false);
    // Currently closed → toggle records true (open).
    store.getState().actions.conversations.toggleChatPane('agent-9', false);
    expect(store.getState().conversations.paneOverrides.get('agent-9')).toBe(true);
    dispose();
  });

  it('ref-swaps the overrides map on each mutation (granularity contract)', () => {
    const { store, dispose } = setup();
    const before = store.getState().conversations.paneOverrides;
    store.getState().actions.conversations.setChatPaneOpen('a', true);
    expect(store.getState().conversations.paneOverrides).not.toBe(before);
    dispose();
  });
});

describe('conversationsActions — pane view modes (TUIchat-3)', () => {
  it('starts with an empty paneViewModes map', () => {
    expect(initialConversationsState.paneViewModes).toEqual({});
  });

  it('setPaneViewMode writes a per-pane mode entry', () => {
    const { store, dispose } = setup();
    store.getState().actions.conversations.setPaneViewMode('agent-1', 'condensed');
    expect(store.getState().conversations.paneViewModes['agent-1']).toBe('condensed');
    store.getState().actions.conversations.setPaneViewMode('agent-1', 'tmux');
    expect(store.getState().conversations.paneViewModes['agent-1']).toBe('tmux');
    dispose();
  });

  it('setPaneViewMode is per-agent (does not touch other panes)', () => {
    const { store, dispose } = setup();
    store.getState().actions.conversations.setPaneViewMode('a', 'tmux');
    store.getState().actions.conversations.setPaneViewMode('b', 'condensed');
    expect(store.getState().conversations.paneViewModes).toEqual({ a: 'tmux', b: 'condensed' });
    dispose();
  });

  it('cyclePaneViewMode rotates verbose → condensed → tmux → verbose from the default', () => {
    const { store, dispose } = setup();
    // No override → effective mode is settings.defaultChatViewMode ('verbose').
    expect(store.getState().settings.defaultChatViewMode).toBe('verbose');
    const cycle = store.getState().actions.conversations.cyclePaneViewMode;
    cycle('a');
    expect(store.getState().conversations.paneViewModes['a']).toBe('condensed');
    cycle('a');
    expect(store.getState().conversations.paneViewModes['a']).toBe('tmux');
    cycle('a');
    expect(store.getState().conversations.paneViewModes['a']).toBe('verbose');
    dispose();
  });

  it('cyclePaneViewMode seeds from the settings default when the pane has no override', () => {
    const { store, dispose } = setup();
    // Make the default 'condensed' — the first cycle should advance condensed → tmux.
    store.setState((s) => ({ settings: { ...s.settings, defaultChatViewMode: 'condensed' } }));
    store.getState().actions.conversations.cyclePaneViewMode('a');
    expect(store.getState().conversations.paneViewModes['a']).toBe('tmux');
    dispose();
  });

  it('ref-swaps paneViewModes on each mutation (granularity contract)', () => {
    const { store, dispose } = setup();
    const before = store.getState().conversations.paneViewModes;
    store.getState().actions.conversations.setPaneViewMode('a', 'tmux');
    expect(store.getState().conversations.paneViewModes).not.toBe(before);
    dispose();
  });
});

// ── chunk-summarized live event (TUIchat-4) ──────────────────────────────────────────────────────

describe('conversations — chunk-summarized event folds into chunkSummaries', () => {
  function chunkEvent(
    agentId: string,
    summaryText: string,
    blockIds: number[],
  ): ConversationBlockEvent {
    return {
      type: 'conversation.block',
      id: `ev-${agentId}-chunk`,
      ts: '2026-06-08T00:00:00Z',
      run_id: 'run-1',
      agent_id: agentId,
      conversation_id: `conv-${agentId}`,
      action: 'chunk-summarized',
      block: { conversation_id: `conv-${agentId}`, summary: summaryText, block_ids: blockIds },
    };
  }

  it('appends a chunk summary into chunkSummaries WITHOUT touching the transcript', () => {
    const { store, dispose } = setup();
    store
      .getState()
      .actions.conversations.applyBlock(
        makeBlockEvent('a', 'assistant', 'block-appended', { text: 'work' }),
      );
    const transcriptBefore = store.getState().conversations.transcripts['a'];
    store.getState().actions.conversations.applyBlock(chunkEvent('a', 'summary one', [1, 2]));
    const conv = store.getState().conversations;
    // Transcript is untouched (chunk summary is NOT a transcript block).
    expect(conv.transcripts['a']).toBe(transcriptBefore);
    expect(conv.chunkSummaries['a']).toHaveLength(1);
    expect(conv.chunkSummaries['a']?.[0]?.summary).toBe('summary one');
    expect(conv.chunkSummaries['a']?.[0]?.blockIds).toEqual([1, 2]);
    dispose();
  });

  it('appends successive summaries with ascending chunkIdx (flush order)', () => {
    const { store, dispose } = setup();
    store.getState().actions.conversations.applyBlock(chunkEvent('a', 'first', [1]));
    store.getState().actions.conversations.applyBlock(chunkEvent('a', 'second', [2]));
    const summaries = store.getState().conversations.chunkSummaries['a'];
    expect(summaries?.map((s) => s.summary)).toEqual(['first', 'second']);
    expect(summaries?.map((s) => s.chunkIdx)).toEqual([0, 1]);
    dispose();
  });

  it('ignores an empty-summary event (Condensed → verbose, no write)', () => {
    const { store, dispose } = setup();
    store.getState().actions.conversations.applyBlock(chunkEvent('a', '', [1]));
    expect(store.getState().conversations.chunkSummaries['a']).toBeUndefined();
    dispose();
  });
});
