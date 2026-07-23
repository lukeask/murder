/**
 * Favorites actions tests — the prefs RPC pipeline (rule 3: actions are the only bus path).
 *
 * Drives the favorites slice through a `FakeApplicationClient`:
 *  - `load()` fires `favorites.get` and fills the slice from the reply.
 *  - `toggle(id)` flips local membership (optimistic) AND fires `favorites.set` with the new id
 *     list — the prefs-persist assertion the C11 DoD requires.
 *  - `setStarred(id, true/false)` is idempotent (no RPC when already in the wanted state).
 *  - a save rejection lands in `error` without rolling back the optimistic local set.
 */

import { beforeEach, describe, expect, it } from 'vitest';
import { FakeApplicationClient } from '../../src/application/FakeApplicationClient.js';
import { createAppStore } from '../../src/store/store.js';
import { selectLiveToasts, toastStore } from '../../src/store/toast/toastStore.js';

/** All live error toasts on the singleton at the current instant. */
function errorToasts() {
  const live = selectLiveToasts(toastStore.getState().toasts, Date.now());
  return live.filter((t) => t.severity === 'error');
}

function setup() {
  const fake = new FakeApplicationClient();
  // Default stubs so an unrelated load/save resolves; tests override as needed.
  fake.stubQuery('favorites.get', { ok: true, favorites: [] });
  fake.stubCommand('favorites.set', { ok: true, favorites: [] });
  fake.stubQuery('roster.get', { invalidation_key: 'iv', sessions: [] });
  const { store, dispose } = createAppStore(fake);
  return { fake, store, dispose };
}

describe('favorites actions', () => {
  // The toast singleton is shared global state; reset it between cases (toastStore's own idiom).
  beforeEach(() => {
    toastStore.getState().clear();
  });

  it('load() fires favorites.get and fills the id set', async () => {
    const { fake, store, dispose } = setup();
    fake.stubQuery('favorites.get', { ok: true, favorites: ['plan-a', 'agent-7'] });

    await store.getState().actions.favorites.load();

    expect(fake.queryCalls.some((c) => c.name === 'favorites.get')).toBe(true);
    const favs = store.getState().favorites;
    expect(favs.status).toBe('ready');
    expect([...favs.ids].sort()).toEqual(['agent-7', 'plan-a']);
    dispose();
  });

  it('toggle(id) flips local membership AND persists via favorites.set', async () => {
    const { fake, store, dispose } = setup();

    // Star.
    await store.getState().actions.favorites.toggle('plan-x');
    expect(store.getState().favorites.ids.has('plan-x')).toBe(true);
    const saveCalls = fake.commandCalls.filter((c) => c.name === 'favorites.set');
    expect(saveCalls.length).toBe(1);
    expect(saveCalls[0]?.params).toEqual({ favorites: ['plan-x'] });

    // Unstar — same id removed, persisted again with the empty list.
    await store.getState().actions.favorites.toggle('plan-x');
    expect(store.getState().favorites.ids.has('plan-x')).toBe(false);
    const saveCalls2 = fake.commandCalls.filter((c) => c.name === 'favorites.set');
    expect(saveCalls2.length).toBe(2);
    expect(saveCalls2[1]?.params).toEqual({ favorites: [] });
    dispose();
  });

  it('setStarred is idempotent — no RPC when already in the wanted state', async () => {
    const { fake, store, dispose } = setup();
    await store.getState().actions.favorites.setStarred('a', true);
    const after1 = fake.commandCalls.filter((c) => c.name === 'favorites.set').length;
    expect(after1).toBe(1);

    // Already starred → no-op, no second save.
    await store.getState().actions.favorites.setStarred('a', true);
    const after2 = fake.commandCalls.filter((c) => c.name === 'favorites.set').length;
    expect(after2).toBe(1);
    dispose();
  });

  it('a save rejection sets error, keeps the optimistic local set, AND surfaces a toast', async () => {
    const { fake, store, dispose } = setup();
    fake.stubCommand('favorites.set', () => {
      throw new Error('rpc error [internal]: bus down');
    });

    await store.getState().actions.favorites.toggle('plan-y');
    const favs = store.getState().favorites;
    // Local set still reflects the user's intent (no rollback).
    expect(favs.ids.has('plan-y')).toBe(true);
    expect(favs.error).toBe('rpc error [internal]: bus down');
    // The fire-and-forget persist rejection has no open form / rendered slice error to host it,
    // so it must surface on the global toast (severity error, the structured message).
    const errs = errorToasts();
    expect(errs).toHaveLength(1);
    expect(errs[0]?.text).toBe('rpc error [internal]: bus down');
    dispose();
  });

  it('a successful toggle persist pushes NO error toast', async () => {
    const { store, dispose } = setup(); // setup() stubs save to resolve
    await store.getState().actions.favorites.toggle('plan-z');
    expect(errorToasts()).toHaveLength(0);
    dispose();
  });

  it('a load rejection sets status=error and leaves favorites empty', async () => {
    const { fake, store, dispose } = setup();
    fake.stubQuery('favorites.get', () => {
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
