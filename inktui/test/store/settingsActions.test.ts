/**
 * Settings actions tests — the `settings.{get,update}` prefs pipeline (rule 3: actions are the only
 * bus path). Mirrors the favorites actions test.
 *
 * Drives the settings slice through a `FakeBusClient`:
 *  - `load()` fires `settings.get` and fills the slice from the reply (wire `key_overrides` →
 *     slice `keyOverrides`).
 *  - `update(partial)` overlays the patch locally (optimistic) AND fires `settings.update` with the
 *     same partial — the persist assertion.
 *  - a save rejection lands in `error` + a toast without rolling back the optimistic local change.
 *  - a load rejection sets status=error and leaves settings at their defaults.
 */

import { beforeEach, describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { createAppStore } from '../../src/store/store.js';
import { selectLiveToasts, toastStore } from '../../src/store/toast/toastStore.js';

/** All live error toasts on the singleton at the current instant. */
function errorToasts() {
  const live = selectLiveToasts(toastStore.getState().toasts, Date.now());
  return live.filter((t) => t.severity === 'error');
}

function setup() {
  const fake = new FakeBusClient();
  // Default stubs so an unrelated load/update resolves; tests override as needed.
  fake.stubRpc('settings.get', {
    ok: true,
    settings: { theme: 'everforest-dark', modifier: 'alt', key_overrides: {} },
  });
  fake.stubRpc('settings.update', {
    ok: true,
    settings: { theme: 'everforest-dark', modifier: 'alt', key_overrides: {} },
  });
  fake.stubRpc('state.crow_snapshot', { invalidation_key: 'iv', sessions: [] });
  const { store, dispose } = createAppStore(fake);
  return { fake, store, dispose };
}

describe('settings actions', () => {
  beforeEach(() => {
    toastStore.getState().clear();
  });

  it('load() fires settings.get and fills the slice (key_overrides → keyOverrides)', async () => {
    const { fake, store, dispose } = setup();
    fake.stubRpc('settings.get', {
      ok: true,
      settings: {
        theme: 'everforest-light',
        modifier: 'ctrl',
        key_overrides: { 'global.spawn': 'x' },
      },
    });

    await store.getState().actions.settings.load();

    expect(fake.rpcCalls.some((c) => c.method === 'settings.get')).toBe(true);
    const s = store.getState().settings;
    expect(s.status).toBe('ready');
    expect(s.theme).toBe('everforest-light');
    expect(s.modifier).toBe('ctrl');
    expect(s.keyOverrides).toEqual({ 'global.spawn': 'x' });
    dispose();
  });

  it('update(partial) overlays locally AND persists via settings.update', async () => {
    const { fake, store, dispose } = setup();

    await store.getState().actions.settings.update({ modifier: 'ctrl' });

    // Optimistic local overlay.
    expect(store.getState().settings.modifier).toBe('ctrl');
    // Persisted with the same partial.
    const updates = fake.rpcCalls.filter((c) => c.method === 'settings.update');
    expect(updates.length).toBe(1);
    expect(updates[0]?.params).toEqual({ settings: { modifier: 'ctrl' } });
    dispose();
  });

  it('update with key_overrides mirrors onto the slice keyOverrides', async () => {
    const { store, dispose } = setup();
    await store.getState().actions.settings.update({ key_overrides: { 'global.tmux': 'g' } });
    expect(store.getState().settings.keyOverrides).toEqual({ 'global.tmux': 'g' });
    dispose();
  });

  it('a save rejection sets error, keeps the optimistic local change, AND surfaces a toast', async () => {
    const { fake, store, dispose } = setup();
    fake.stubRpc('settings.update', () => {
      throw new Error('rpc error [internal]: bus down');
    });

    await store.getState().actions.settings.update({ modifier: 'both' });
    const s = store.getState().settings;
    // Local change still reflects the user's intent (no rollback).
    expect(s.modifier).toBe('both');
    expect(s.error).toBe('rpc error [internal]: bus down');
    const errs = errorToasts();
    expect(errs).toHaveLength(1);
    expect(errs[0]?.text).toBe('rpc error [internal]: bus down');
    dispose();
  });

  it('a successful update pushes NO error toast', async () => {
    const { store, dispose } = setup();
    await store.getState().actions.settings.update({ theme: 'everforest-light' });
    expect(errorToasts()).toHaveLength(0);
    dispose();
  });

  it('a load rejection sets status=error and leaves settings at defaults', async () => {
    const { fake, store, dispose } = setup();
    fake.stubRpc('settings.get', () => {
      throw new Error('no settings');
    });

    await store.getState().actions.settings.load();
    const s = store.getState().settings;
    expect(s.status).toBe('error');
    expect(s.error).toBe('no settings');
    expect(s.modifier).toBe('alt');
    expect(s.theme).toBe('everforest-dark');
    expect(s.keyOverrides).toEqual({});
    dispose();
  });
});
