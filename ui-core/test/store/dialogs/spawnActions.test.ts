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
import { FakeApplicationClient } from '@murder/ui-core/application/FakeApplicationClient.js';
import { createSpawnActions } from '@murder/ui-core/store/dialogs/spawnActions.js';
import type { AppStore } from '@murder/ui-core/store/store.js';

/** A bus that returns the live command result directly, matching the application protocol. */
function spawnBus(agentId: string | undefined): FakeApplicationClient {
  const bus = new FakeApplicationClient();
  bus.stubCommand(
    'crow.spawn_rogue',
    agentId !== undefined ? { handled: true, agent_id: agentId } : { handled: true },
  );
  return bus;
}

/** Minimal store stub exposing only the actions `spawnRogue` touches, each a spy. */
function fakeStore(): {
  store: StoreApi<AppStore>;
  refresh: ReturnType<typeof vi.fn>;
  setTranscriptPaneOpen: ReturnType<typeof vi.fn>;
  setActivePaneAgentId: ReturnType<typeof vi.fn>;
} {
  const refresh = vi.fn(() => Promise.resolve());
  const setTranscriptPaneOpen = vi.fn();
  const setActivePaneAgentId = vi.fn();
  const state = {
    actions: {
      roster: { refresh },
      conversations: { setTranscriptPaneOpen, setActivePaneAgentId },
    },
  };
  const store = { getState: () => state } as unknown as StoreApi<AppStore>;
  return { store, refresh, setTranscriptPaneOpen, setActivePaneAgentId };
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
