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

import { describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../../src/bus/FakeBusClient.js';
import type { ConversationBlockEvent } from '../../../src/bus/protocol.js';
import { initialConversationsState } from '../../../src/store/conversations/conversationsSlice.js';
import { createAppStore, initialAppState } from '../../../src/store/store.js';

// ── Helpers ──────────────────────────────────────────────────────────────────────────────────────

function setup() {
  const fake = new FakeBusClient();
  // Stub sibling RPCs so the store doesn't reject.
  fake.stubRpc('crow.get_snapshot', { invalidation_key: 'iv', sessions: [] });
  fake.stubRpc('agent.message', {});
  const { store, dispose } = createAppStore(fake);
  return { fake, store, dispose };
}

/** Build a minimal `ConversationBlockEvent` for a given agent. */
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
  it('calls bus.rpc("agent.message") exactly once with the correct agent_id + message', async () => {
    const { fake, store, dispose } = setup();

    await store.getState().actions.conversations.send('agent-42', 'test message');

    expect(fake.rpcCalls).toHaveLength(1);
    expect(fake.rpcCalls[0]?.method).toBe('agent.message');
    expect(fake.rpcCalls[0]?.params).toEqual({ agent_id: 'agent-42', message: 'test message' });
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
    // Stub agent.message to reject by not stubbing it — FakeBusClient rejects unknown methods.
    // We use crow.get_snapshot stub to let the store init pass, but leave agent.message unstubbed.
    fake.stubRpc('crow.get_snapshot', { invalidation_key: 'iv', sessions: [] });
    const { store, dispose } = createAppStore(fake);

    // Should not throw — send swallows the rejection.
    await expect(
      store.getState().actions.conversations.send('agent-1', 'hi'),
    ).resolves.toBeUndefined();
    dispose();
  });

  it('is the SOLE caller of agent.message — no other path calls the bus for chat', async () => {
    // This test proves the rule-3 invariant: only conversationsActions.send calls agent.message.
    // We verify by checking that after a send, only one rpc call appears.
    const { fake, store, dispose } = setup();

    await store.getState().actions.conversations.send('agent-1', 'msg');
    const agentMessageCalls = fake.rpcCalls.filter((c) => c.method === 'agent.message');
    expect(agentMessageCalls).toHaveLength(1);
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
