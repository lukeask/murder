/**
 * Factory-level unit tests for the shared list-slice mechanics ({@link createRefreshAction}).
 *
 * The store-core granularity proofs in `store.test.ts` exercise this factory end-to-end through
 * every real domain slice (matching event → 1 rpc + ref-swap-only-this-key, sibling identity
 * preserved, error→slice.error, loading flag). These tests pin the factory's contract directly,
 * in isolation, against the roster slice as a representative — so a regression in the shared
 * mechanics is caught at the factory boundary, not only via a domain.
 */

import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import type { CrowSnapshotReply } from '../../src/store/roster/rosterActions.js';
import { createAppStore } from '../../src/store/store.js';

function crowReply(): CrowSnapshotReply {
  return {
    invalidation_key: 'iv-1',
    sessions: [{ agent_id: 'a-1', role: 'crow', status: 'running' }],
  };
}

async function flush(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

describe('createRefreshAction — shared list-slice mechanics', () => {
  it('projects the reply into rows and flips the slice to ready', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', crowReply());
    const { store } = createAppStore(fake);

    await store.getState().actions.roster.refresh();

    expect(store.getState().roster.status).toBe('ready');
    expect(store.getState().roster.error).toBeNull();
    expect(store.getState().roster.rows).toHaveLength(1);
    expect(store.getState().roster.rows[0]?.agentId).toBe('a-1');
  });

  it('issues exactly one rpc for the slice it is bound to', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', crowReply());
    const { store } = createAppStore(fake);

    await store.getState().actions.roster.refresh();

    expect(fake.rpcCalls).toEqual([{ method: 'state.crow_snapshot', params: {} }]);
  });

  it('ref-swaps ONLY its own slice key — siblings keep identity', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', crowReply());
    const { store } = createAppStore(fake);
    const notesBefore = store.getState().notes;
    const reportsBefore = store.getState().reports;
    const ticketsBefore = store.getState().tickets;
    const rosterBefore = store.getState().roster;

    await store.getState().actions.roster.refresh();

    expect(store.getState().roster).not.toBe(rosterBefore);
    expect(store.getState().notes).toBe(notesBefore);
    expect(store.getState().reports).toBe(reportsBefore);
    expect(store.getState().tickets).toBe(ticketsBefore);
  });

  it('routes a rejected rpc into the slice error field, never thrown past the action', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', () => {
      throw new Error('bus down');
    });
    const { store } = createAppStore(fake);

    // Must not reject — the factory swallows the error into the slice.
    await expect(store.getState().actions.roster.refresh()).resolves.toBeUndefined();
    expect(store.getState().roster.status).toBe('error');
    expect(store.getState().roster.error).toBe('bus down');
  });

  it('coalesces a synchronous burst of refresh() into exactly ONE rpc', async () => {
    // A cold-start `state.snapshot` storm can fire refresh() once per ticket (~130x). The microtask
    // deferral lets the whole synchronous burst bump `seq` to its final value before any RPC fires,
    // so every stale token short-circuits BEFORE hitting the wire — only the last call issues an RPC.
    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', crowReply());
    const { store } = createAppStore(fake);
    const refresh = store.getState().actions.roster.refresh;

    // Fire a synchronous burst (no awaits between calls) — the realistic invalidation-storm shape.
    const pending = Promise.all([refresh(), refresh(), refresh(), refresh(), refresh()]);
    await pending;
    await flush();

    expect(fake.rpcCalls).toEqual([{ method: 'state.crow_snapshot', params: {} }]);
    // The surviving (latest) call still runs to completion — the slice is not stranded in `loading`.
    expect(store.getState().roster.status).toBe('ready');
    expect(store.getState().roster.rows).toHaveLength(1);
  });

  it('a stale (superseded) refresh still flashes loading before short-circuiting', async () => {
    // The early token check is AFTER the loading setState, so a burst still shows loading — only the
    // RPC is skipped for stale tokens. Verify the loading flag is set synchronously by the burst.
    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', crowReply());
    const { store } = createAppStore(fake);
    const refresh = store.getState().actions.roster.refresh;

    const pending = Promise.all([refresh(), refresh()]);
    // Before the microtask resolves, the slice is already loading (loading setState ran synchronously
    // for every burst call, ahead of the deferred RPC).
    expect(store.getState().roster.status).toBe('loading');
    await pending;
    await flush();
    expect(store.getState().roster.status).toBe('ready');
  });

  it('marks the slice loading before the rpc resolves', async () => {
    let resolveReply: (r: CrowSnapshotReply) => void = () => {};
    const fake = new FakeBusClient();
    fake.stubRpc(
      'state.crow_snapshot',
      () =>
        new Promise<CrowSnapshotReply>((resolve) => {
          resolveReply = resolve;
        }),
    );
    const { store } = createAppStore(fake);

    const pending = store.getState().actions.roster.refresh();
    await flush();
    expect(store.getState().roster.status).toBe('loading');

    resolveReply(crowReply());
    await pending;
    expect(store.getState().roster.status).toBe('ready');
  });
});
