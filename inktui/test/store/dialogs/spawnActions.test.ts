/**
 * spawnActions tests — the rogue-crow spawn path (the ONLY spawn caller, rule 3).
 *
 * Focus: a successful spawn (with a store provided) proactively re-pulls the roster so the freshly
 * spawned crow appears in the Crows panel immediately, rather than waiting for the next
 * `state.snapshot`/`entity:'agent'` event to drive the snapshot invalidation. Also asserts the
 * store-less construction stays inert (no crash, no roster call).
 */

import { describe, expect, it, vi } from 'vitest';
import type { StoreApi } from 'zustand';
import { FakeBusClient } from '../../../src/bus/FakeBusClient.js';
import { createSpawnActions } from '../../../src/store/dialogs/spawnActions.js';
import type { AppStore } from '../../../src/store/store.js';

/** A bus that accepts a `crow.spawn_rogue` command and returns `agentId` from its terminal status. */
function spawnBus(agentId: string | undefined): FakeBusClient {
  const bus = new FakeBusClient();
  bus.stubRpc('command.submit', { ok: true, command_id: 'cmd-1' });
  bus.stubRpc('command.status', {
    ok: true,
    status: 'done',
    result_json: JSON.stringify(agentId !== undefined ? { handled: true, agent_id: agentId } : {}),
  });
  return bus;
}

/** Minimal store stub exposing only the actions `spawnRogue` touches, each a spy. */
function fakeStore(): {
  store: StoreApi<AppStore>;
  refresh: ReturnType<typeof vi.fn>;
  setChatPaneOpen: ReturnType<typeof vi.fn>;
  setActivePaneAgentId: ReturnType<typeof vi.fn>;
} {
  const refresh = vi.fn(() => Promise.resolve());
  const setChatPaneOpen = vi.fn();
  const setActivePaneAgentId = vi.fn();
  const state = {
    actions: {
      roster: { refresh },
      conversations: { setChatPaneOpen, setActivePaneAgentId },
    },
  };
  const store = { getState: () => state } as unknown as StoreApi<AppStore>;
  return { store, refresh, setChatPaneOpen, setActivePaneAgentId };
}

describe('spawnActions — spawnRogue roster refresh', () => {
  it('proactively refreshes the roster after a successful spawn (store provided)', async () => {
    const { store, refresh } = fakeStore();
    const actions = createSpawnActions(spawnBus('rogue-7'), store);

    const result = await actions.spawnRogue({ harness: 'claude_code', model: 'opus' });

    expect(result.agent_id).toBe('rogue-7');
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it('does not refresh the roster when the spawn returns no agent_id', async () => {
    const { store, refresh } = fakeStore();
    const actions = createSpawnActions(spawnBus(undefined), store);

    await actions.spawnRogue({ harness: 'claude_code', model: 'opus' });

    expect(refresh).not.toHaveBeenCalled();
  });

  it('is inert (no crash, no roster call) when constructed without a store', async () => {
    const actions = createSpawnActions(spawnBus('rogue-9'));
    const result = await actions.spawnRogue({ harness: 'claude_code', model: 'opus' });
    expect(result.agent_id).toBe('rogue-9');
  });
});
