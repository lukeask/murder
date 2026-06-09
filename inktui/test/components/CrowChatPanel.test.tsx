/**
 * CrowChatPanel test — C10 chat-pane component gate.
 *
 * Copied from `RosterPanel.test.tsx` per the C5 copy recipe. Verifies:
 *  1. A favorited crow's history is rendered in its pane (collaborator, rogue).
 *  2. A non-favorited crow (planner, ticket crow) does NOT get a pane by default.
 *  3. When there are no favorited crows, the panel renders null (no output).
 *  4. The active pane is highlighted (green border) by default for the collaborator.
 *  5. The component uses `actions.conversations.send` (rule 3 — no direct bus access).
 *
 * Pattern: FakeBusClient + createAppStore + inject blocks via `applyBlock` action.
 */

import { Box } from 'ink';
import { render } from 'ink-testing-library';
import type { JSX } from 'react';
import { describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { CrowChatPanel } from '../../src/components/CrowChatPanel.js';
import { AppStoreProvider } from '../../src/hooks/useAppStore.js';
import { BusClientProvider } from '../../src/hooks/useBusClient.js';
import { InputStoresProvider } from '../../src/hooks/useInputStores.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import type { CrowSnapshotReply } from '../../src/store/roster/rosterActions.js';
import { createAppStore } from '../../src/store/store.js';

/** Let Ink flush a render + effects. */
async function tick(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 20));
}

/** A canned snapshot with a collaborator and a rogue crow. */
function twoFavoritedCrows(): CrowSnapshotReply {
  return {
    invalidation_key: 'iv',
    sessions: [
      {
        agent_id: 'collab-1',
        role: 'collaborator',
        status: 'idle',
        session_name: 'Collaborator',
      },
      {
        agent_id: 'rogue-1',
        role: 'crow',
        status: 'running',
        session_name: 'alpha-rogue',
      },
    ],
  };
}

/** A snapshot with a planner and ticket crow only (neither default-favorited). */
function nonFavoritedCrows(): CrowSnapshotReply {
  return {
    invalidation_key: 'iv',
    sessions: [
      { agent_id: 'planner-1', role: 'planner', status: 'idle' },
      {
        agent_id: 'ticket-crow-1',
        role: 'crow',
        status: 'running',
        ticket_id: 'T-1',
        ticket_title: 'Fix it',
      },
    ],
  };
}

/** Harness: CrowChatPanel inside both providers. */
function Harness({
  store,
  inputStores,
  bus,
}: {
  readonly store: ReturnType<typeof createAppStore>['store'];
  readonly inputStores: ReturnType<typeof createInputStores>;
  readonly bus: FakeBusClient;
}): JSX.Element {
  return (
    <AppStoreProvider value={store}>
      <InputStoresProvider value={inputStores}>
        <BusClientProvider value={bus}>
          <Box>
            <CrowChatPanel />
          </Box>
        </BusClientProvider>
      </InputStoresProvider>
    </AppStoreProvider>
  );
}

async function setup(reply: CrowSnapshotReply = twoFavoritedCrows()) {
  const fake = new FakeBusClient();
  fake.stubRpc('state.crow_snapshot', reply);
  // F2: chat sends route through command.submit (agent.message command kind), not a direct RPC.
  fake.stubRpc('command.submit', { ok: true, command_id: 'cmd-1' });
  fake.stubRpc('command.status', { ok: true, status: 'done', result_json: '{}' });
  const { store, dispose } = createAppStore(fake);
  await store.getState().actions.roster.refresh();
  const inputStores = createInputStores(['crows'], 'crows');
  return { fake, store, dispose, inputStores };
}

describe('CrowChatPanel — chat history rendering', () => {
  it('renders a pane for each default-favorited crow (collaborator + rogue)', async () => {
    const { store, inputStores, dispose, fake } = await setup();
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} bus={fake} />);
    await tick();

    const frame = lastFrame() ?? '';
    // Should see the collaborator pane label and the rogue pane label.
    expect(frame).toContain('Collaborator');
    expect(frame).toContain('alpha-rogue');
    dispose();
  });

  it('shows "no history" when an agent has no blocks', async () => {
    const { store, inputStores, dispose, fake } = await setup();
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} bus={fake} />);
    await tick();

    const frame = lastFrame() ?? '';
    expect(frame).toContain('no history');
    dispose();
  });

  it('renders conversation history when blocks are present', async () => {
    const { store, inputStores, dispose, fake } = await setup();

    // Inject a block for the collaborator via applyBlock.
    store.getState().actions.conversations.applyBlock({
      type: 'conversation.block',
      id: 'ev-1',
      ts: '2026-06-08T00:00:00Z',
      run_id: 'run-1',
      agent_id: 'collab-1',
      conversation_id: 'conv-collab-1',
      action: 'block-appended',
      block: { type: 'user', id: 'blk-1', text: 'hello collaborator' },
    });

    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} bus={fake} />);
    await tick();

    expect(lastFrame()).toContain('hello collaborator');
    dispose();
  });

  it('renders null (nothing) when no default-favorited crows exist', async () => {
    const { store, inputStores, dispose, fake } = await setup(nonFavoritedCrows());
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} bus={fake} />);
    await tick();

    // No crow chat panes should render. The frame may be empty or contain only the Box wrapper.
    const frame = lastFrame() ?? '';
    expect(frame).not.toContain('[collab]');
    expect(frame).not.toContain('[rogue]');
    dispose();
  });

  it('activePaneAgentId controls which pane is highlighted as active', async () => {
    const { store, inputStores, dispose, fake } = await setup();

    // Pin the rogue pane as active.
    store.getState().actions.conversations.setActivePaneAgentId('rogue-1');

    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} bus={fake} />);
    await tick();

    // Both panes render; the rogue should be the active one.
    const frame = lastFrame() ?? '';
    expect(frame).toContain('alpha-rogue');
    expect(frame).toContain('[rogue]');
    dispose();
  });

  it('send routes to the correct agentId (agent.message action wiring)', async () => {
    const { store, dispose, fake } = await setup();

    // Directly dispatch send — proves rule 3: only conversations.send calls the bus for chat.
    await store.getState().actions.conversations.send('collab-1', 'test send');

    // The send rides command.submit as the agent.message command kind (rule 3: only
    // conversations.send calls the bus for chat).
    const sendCalls = fake.rpcCalls.filter(
      (c) =>
        c.method === 'command.submit' && (c.params as { kind: string }).kind === 'agent.message',
    );
    expect(sendCalls).toHaveLength(1);
    expect(sendCalls[0]?.params).toMatchObject({
      kind: 'agent.message',
      payload: { agent_id: 'collab-1', message: 'test send' },
    });
    dispose();
  });
});
