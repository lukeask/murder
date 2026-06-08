/**
 * Favorites actions tests — the prefs RPC pipeline (rule 3: actions are the only bus path).
 *
 * Drives the favorites slice through a `FakeBusClient`:
 *  - `load()` fires `tui.load_favorites` and fills the slice from the reply.
 *  - `toggle(id)` flips local membership (optimistic) AND fires `tui.save_favorites` with the new id
 *     list — the prefs-persist assertion the C11 DoD requires.
 *  - `setStarred(id, true/false)` is idempotent (no RPC when already in the wanted state).
 *  - a save rejection lands in `error` without rolling back the optimistic local set.
 */

import { describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { createAppStore } from '../../src/store/store.js';

function setup() {
  const fake = new FakeBusClient();
  // Default stubs so an unrelated load/save resolves; tests override as needed.
  fake.stubRpc('tui.load_favorites', { ok: true, favorites: [] });
  fake.stubRpc('tui.save_favorites', { ok: true, favorites: [] });
  fake.stubRpc('crow.get_snapshot', { invalidation_key: 'iv', sessions: [] });
  const { store, dispose } = createAppStore(fake);
  return { fake, store, dispose };
}

describe('favorites actions', () => {
  it('load() fires tui.load_favorites and fills the id set', async () => {
    const { fake, store, dispose } = setup();
    fake.stubRpc('tui.load_favorites', { ok: true, favorites: ['plan-a', 'agent-7'] });

    await store.getState().actions.favorites.load();

    expect(fake.rpcCalls.some((c) => c.method === 'tui.load_favorites')).toBe(true);
    const favs = store.getState().favorites;
    expect(favs.status).toBe('ready');
    expect([...favs.ids].sort()).toEqual(['agent-7', 'plan-a']);
    dispose();
  });

  it('toggle(id) flips local membership AND persists via tui.save_favorites', async () => {
    const { fake, store, dispose } = setup();

    // Star.
    await store.getState().actions.favorites.toggle('plan-x');
    expect(store.getState().favorites.ids.has('plan-x')).toBe(true);
    const saveCalls = fake.rpcCalls.filter((c) => c.method === 'tui.save_favorites');
    expect(saveCalls.length).toBe(1);
    expect(saveCalls[0]?.params).toEqual({ favorites: ['plan-x'] });

    // Unstar — same id removed, persisted again with the empty list.
    await store.getState().actions.favorites.toggle('plan-x');
    expect(store.getState().favorites.ids.has('plan-x')).toBe(false);
    const saveCalls2 = fake.rpcCalls.filter((c) => c.method === 'tui.save_favorites');
    expect(saveCalls2.length).toBe(2);
    expect(saveCalls2[1]?.params).toEqual({ favorites: [] });
    dispose();
  });

  it('setStarred is idempotent — no RPC when already in the wanted state', async () => {
    const { fake, store, dispose } = setup();
    await store.getState().actions.favorites.setStarred('a', true);
    const after1 = fake.rpcCalls.filter((c) => c.method === 'tui.save_favorites').length;
    expect(after1).toBe(1);

    // Already starred → no-op, no second save.
    await store.getState().actions.favorites.setStarred('a', true);
    const after2 = fake.rpcCalls.filter((c) => c.method === 'tui.save_favorites').length;
    expect(after2).toBe(1);
    dispose();
  });

  it('a save rejection sets error but keeps the optimistic local set', async () => {
    const { fake, store, dispose } = setup();
    fake.stubRpc('tui.save_favorites', () => {
      throw new Error('bus down');
    });

    await store.getState().actions.favorites.toggle('plan-y');
    const favs = store.getState().favorites;
    // Local set still reflects the user's intent (no rollback).
    expect(favs.ids.has('plan-y')).toBe(true);
    expect(favs.error).toBe('bus down');
    dispose();
  });

  it('a load rejection sets status=error and leaves favorites empty', async () => {
    const { fake, store, dispose } = setup();
    fake.stubRpc('tui.load_favorites', () => {
      throw new Error('no prefs');
    });

    await store.getState().actions.favorites.load();
    const favs = store.getState().favorites;
    expect(favs.status).toBe('error');
    expect(favs.error).toBe('no prefs');
    expect(favs.ids.size).toBe(0);
    dispose();
  });
});
