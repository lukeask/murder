/**
 * Templates actions tests — the template-registry RPC pipeline (rule 3: actions are the only bus
 * path).
 *
 * Drives the templates slice through a `FakeBusClient`:
 *  - `load()` fires `tui.load_templates` and fills the slice from the reply.
 *  - `save(name, body)` upserts locally (optimistic) AND fires `tui.save_templates`, then SYNCS the
 *     slice to the server's normalized echo.
 *  - `remove(name)` / `rename(old, new)` mutate locally + persist.
 *  - a save rejection lands in `error` + toast without rolling back the optimistic local list.
 *  - a load rejection sets status=error and leaves the registry empty.
 */

import { beforeEach, describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { createAppStore } from '../../src/store/store.js';
import { selectTemplatesByName } from '../../src/store/templates/templatesSlice.js';
import { selectLiveToasts, toastStore } from '../../src/store/toast/toastStore.js';

/** All live error toasts on the singleton at the current instant. */
function errorToasts() {
  const live = selectLiveToasts(toastStore.getState().toasts, Date.now());
  return live.filter((t) => t.severity === 'error');
}

function setup() {
  const fake = new FakeBusClient();
  // Default stubs so an unrelated load/save resolves; tests override as needed. The save stub
  // echoes back the submitted templates (the backend normalizes; tests that care override this).
  fake.stubRpc('tui.load_templates', { ok: true, templates: [] });
  fake.stubRpc('tui.save_templates', (params) => ({ ok: true, templates: params.templates }));
  fake.stubRpc('state.crow_snapshot', { invalidation_key: 'iv', sessions: [] });
  const { store, dispose } = createAppStore(fake);
  return { fake, store, dispose };
}

describe('templates actions', () => {
  // The toast singleton is shared global state; reset it between cases (toastStore's own idiom).
  beforeEach(() => {
    toastStore.getState().clear();
  });

  it('load() fires tui.load_templates and fills the registry', async () => {
    const { fake, store, dispose } = setup();
    fake.stubRpc('tui.load_templates', {
      ok: true,
      templates: [
        { name: 'greet', body: 'hello' },
        { name: 'bye', body: 'goodbye' },
      ],
    });

    await store.getState().actions.templates.load();

    expect(fake.rpcCalls.some((c) => c.method === 'tui.load_templates')).toBe(true);
    const { templates } = store.getState();
    expect(templates.status).toBe('ready');
    expect(templates.items).toEqual([
      { name: 'greet', body: 'hello' },
      { name: 'bye', body: 'goodbye' },
    ]);
    dispose();
  });

  it('save() upserts locally AND syncs to the server-normalized echo', async () => {
    const { fake, store, dispose } = setup();
    // Server normalizes: sort by name. The save stub returns a sorted list to prove the slice syncs
    // to the RETURNED list, not the optimistic one.
    fake.stubRpc('tui.save_templates', (params) => ({
      ok: true,
      templates: [...params.templates].sort((a, b) => a.name.localeCompare(b.name)),
    }));

    await store.getState().actions.templates.save('zed', 'Z');
    await store.getState().actions.templates.save('alpha', 'A');

    const saveCalls = fake.rpcCalls.filter((c) => c.method === 'tui.save_templates');
    expect(saveCalls.length).toBe(2);
    // Slice reflects the normalized (sorted) echo, not insertion order.
    expect(store.getState().templates.items).toEqual([
      { name: 'alpha', body: 'A' },
      { name: 'zed', body: 'Z' },
    ]);
    dispose();
  });

  it('save() replaces the body when the name already exists (upsert)', async () => {
    const { store, dispose } = setup();
    await store.getState().actions.templates.save('greet', 'hi');
    await store.getState().actions.templates.save('greet', 'hello again');
    expect(store.getState().templates.items).toEqual([{ name: 'greet', body: 'hello again' }]);
    dispose();
  });

  it('remove() deletes by name and persists the reduced list', async () => {
    const { fake, store, dispose } = setup();
    await store.getState().actions.templates.save('a', '1');
    await store.getState().actions.templates.save('b', '2');

    await store.getState().actions.templates.remove('a');

    expect(store.getState().templates.items).toEqual([{ name: 'b', body: '2' }]);
    const lastSave = fake.rpcCalls.filter((c) => c.method === 'tui.save_templates').at(-1);
    expect(lastSave?.params).toEqual({ templates: [{ name: 'b', body: '2' }] });
    dispose();
  });

  it('rename() preserves the body and persists', async () => {
    const { store, dispose } = setup();
    await store.getState().actions.templates.save('old', 'keep me');

    await store.getState().actions.templates.rename('old', 'new');

    expect(store.getState().templates.items).toEqual([{ name: 'new', body: 'keep me' }]);
    dispose();
  });

  it('rename() is a no-op (no RPC) when the old name is absent', async () => {
    const { fake, store, dispose } = setup();
    await store.getState().actions.templates.rename('missing', 'whatever');
    expect(fake.rpcCalls.filter((c) => c.method === 'tui.save_templates').length).toBe(0);
    dispose();
  });

  it('a save rejection sets error, keeps the optimistic list, AND surfaces a toast', async () => {
    const { fake, store, dispose } = setup();
    fake.stubRpc('tui.save_templates', () => {
      throw new Error('rpc error [internal]: bus down');
    });

    await store.getState().actions.templates.save('t', 'body');
    const { templates } = store.getState();
    // Local list still reflects the user's intent (no rollback).
    expect(templates.items).toEqual([{ name: 't', body: 'body' }]);
    expect(templates.error).toBe('rpc error [internal]: bus down');
    const errs = errorToasts();
    expect(errs).toHaveLength(1);
    expect(errs[0]?.text).toBe('rpc error [internal]: bus down');
    dispose();
  });

  it('a successful save pushes NO error toast', async () => {
    const { store, dispose } = setup();
    await store.getState().actions.templates.save('ok', 'fine');
    expect(errorToasts()).toHaveLength(0);
    dispose();
  });

  it('a load rejection sets status=error and leaves the registry empty', async () => {
    const { fake, store, dispose } = setup();
    fake.stubRpc('tui.load_templates', () => {
      throw new Error('no templates');
    });

    await store.getState().actions.templates.load();
    const { templates } = store.getState();
    expect(templates.status).toBe('error');
    expect(templates.error).toBe('no templates');
    expect(templates.items).toEqual([]);
    dispose();
  });

  it('selectTemplatesByName indexes by name (last-wins)', () => {
    const map = selectTemplatesByName([
      { name: 'a', body: '1' },
      { name: 'b', body: '2' },
      { name: 'a', body: '3' },
    ]);
    expect(map.size).toBe(2);
    expect(map.get('a')).toEqual({ name: 'a', body: '3' });
    expect(map.get('b')).toEqual({ name: 'b', body: '2' });
  });
});
