/**
 * Workflows actions tests — the workflow-registry RPC pipeline (rule 3: actions are the only bus path).
 *
 * Drives the workflows slice through a `FakeBusClient`:
 *  - `load()` fires `workflows.get` and fills the slice from the reply.
 *  - `save(defn)` upserts locally (optimistic) AND fires `workflows.set`, then SYNCS the slice to
 *     the server's normalized echo.
 *  - `remove(name)` / `rename(old, new)` mutate locally + persist.
 *  - `run(name, args)` fires `workflow.start` with `{name, args}` and toasts the run ticket on success.
 *  - a run failure / save rejection lands in a toast (run is fire-and-forget; save keeps the optimistic
 *     local list without rollback).
 */

import { beforeEach, describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { createAppStore } from '../../src/store/store.js';
import { selectLiveToasts, toastStore } from '../../src/store/toast/toastStore.js';
import type { WorkflowDef } from '../../src/store/workflows/workflowsSlice.js';
import { selectWorkflowsByName } from '../../src/store/workflows/workflowsSlice.js';

/** All live error toasts on the singleton at the current instant. */
function errorToasts() {
  const live = selectLiveToasts(toastStore.getState().toasts, Date.now());
  return live.filter((t) => t.severity === 'error');
}

/** All live toasts (any severity) at the current instant. */
function liveToasts() {
  return selectLiveToasts(toastStore.getState().toasts, Date.now());
}

/** A minimal, fully-shaped workflow def for tests (one trivial stage). */
function wf(name: string, description = ''): WorkflowDef {
  return {
    name,
    description,
    mode: 'static',
    stages: [
      {
        id: 's1',
        title: 'stage one',
        instructions: 'do {input}',
        harness: 'crow',
        model: '',
        worktree: '',
        depends_on: [],
        gate: '',
      },
    ],
  };
}

function setup() {
  const fake = new FakeBusClient();
  // Default stubs so an unrelated load/save/run resolves; tests override as needed. The save stub
  // echoes back the submitted workflows (the backend normalizes; tests that care override this).
  fake.stubQuery('workflows.get', { ok: true, workflows: [] });
  fake.stubCommand('workflows.set', (params) => ({ ok: true, workflows: params.workflows }));
  fake.stubCommand('workflow.start', {
    ok: true,
    run_ticket_id: 'T-run',
    stage_ticket_ids: {},
    created_ticket_ids: [],
  });
  fake.stubQuery('roster.get', { invalidation_key: 'iv', sessions: [] });
  const { store, dispose } = createAppStore(fake);
  return { fake, store, dispose };
}

describe('workflows actions', () => {
  // The toast singleton is shared global state; reset it between cases (toastStore's own idiom).
  beforeEach(() => {
    toastStore.getState().clear();
  });

  it('load() fires workflows.get and fills the registry', async () => {
    const { fake, store, dispose } = setup();
    fake.stubQuery('workflows.get', { ok: true, workflows: [wf('alpha'), wf('beta')] });

    await store.getState().actions.workflows.load();

    expect(fake.queryCalls.some((c) => c.name === 'workflows.get')).toBe(true);
    const { workflows } = store.getState();
    expect(workflows.status).toBe('ready');
    expect(workflows.items.map((w) => w.name)).toEqual(['alpha', 'beta']);
    dispose();
  });

  it('save() upserts locally AND syncs to the server-normalized echo', async () => {
    const { fake, store, dispose } = setup();
    // Server normalizes: sort by name. The save stub returns a sorted list to prove the slice syncs
    // to the RETURNED list, not the optimistic one.
    fake.stubCommand('workflows.set', (params) => ({
      ok: true,
      workflows: [...params.workflows].sort((a, b) => a.name.localeCompare(b.name)),
    }));

    await store.getState().actions.workflows.save(wf('zed'));
    await store.getState().actions.workflows.save(wf('alpha'));

    const saveCalls = fake.commandCalls.filter((c) => c.name === 'workflows.set');
    expect(saveCalls.length).toBe(2);
    // Slice reflects the normalized (sorted) echo, not insertion order.
    expect(store.getState().workflows.items.map((w) => w.name)).toEqual(['alpha', 'zed']);
    dispose();
  });

  it('save() replaces the def when the name already exists (upsert)', async () => {
    const { store, dispose } = setup();
    await store.getState().actions.workflows.save(wf('grow', 'first'));
    await store.getState().actions.workflows.save(wf('grow', 'second'));
    expect(store.getState().workflows.items).toEqual([wf('grow', 'second')]);
    dispose();
  });

  it('remove() deletes by name and persists the reduced list', async () => {
    const { fake, store, dispose } = setup();
    await store.getState().actions.workflows.save(wf('a'));
    await store.getState().actions.workflows.save(wf('b'));

    await store.getState().actions.workflows.remove('a');

    expect(store.getState().workflows.items.map((w) => w.name)).toEqual(['b']);
    const lastSave = fake.commandCalls.filter((c) => c.name === 'workflows.set').at(-1);
    expect(lastSave?.params).toEqual({ workflows: [wf('b')] });
    dispose();
  });

  it('rename() preserves the def body and persists', async () => {
    const { store, dispose } = setup();
    await store.getState().actions.workflows.save(wf('old', 'keep me'));

    await store.getState().actions.workflows.rename('old', 'new');

    expect(store.getState().workflows.items).toEqual([wf('new', 'keep me')]);
    dispose();
  });

  it('rename() is a no-op (no RPC) when the old name is absent', async () => {
    const { fake, store, dispose } = setup();
    await store.getState().actions.workflows.rename('missing', 'whatever');
    expect(fake.commandCalls.filter((c) => c.name === 'workflows.set').length).toBe(0);
    dispose();
  });

  it('run() fires workflow.start with {name, args} and toasts the run ticket on success', async () => {
    const { fake, store, dispose } = setup();

    await store.getState().actions.workflows.run('wf', { input: 'do stuff' });

    const runCall = fake.commandCalls.find((c) => c.name === 'workflow.start');
    expect(runCall?.params).toEqual({ name: 'wf', args: { input: 'do stuff' } });
    const toasts = liveToasts();
    expect(toasts).toHaveLength(1);
    expect(toasts[0]?.text).toBe('fired :wf: → T-run');
    expect(errorToasts()).toHaveLength(0);
    dispose();
  });

  it('run() failure surfaces the error via a toast', async () => {
    const { fake, store, dispose } = setup();
    fake.stubCommand('workflow.start', () => {
      throw new Error('rpc error [internal]: no such workflow');
    });

    await store.getState().actions.workflows.run('ghost', {});

    const errs = errorToasts();
    expect(errs).toHaveLength(1);
    expect(errs[0]?.text).toBe('rpc error [internal]: no such workflow');
    dispose();
  });

  it('a save rejection sets error, keeps the optimistic list, AND surfaces a toast', async () => {
    const { fake, store, dispose } = setup();
    fake.stubCommand('workflows.set', () => {
      throw new Error('rpc error [internal]: bus down');
    });

    await store.getState().actions.workflows.save(wf('t'));
    const { workflows } = store.getState();
    // Local list still reflects the user's intent (no rollback).
    expect(workflows.items).toEqual([wf('t')]);
    expect(workflows.error).toBe('rpc error [internal]: bus down');
    const errs = errorToasts();
    expect(errs).toHaveLength(1);
    expect(errs[0]?.text).toBe('rpc error [internal]: bus down');
    dispose();
  });

  it('a load rejection sets status=error and leaves the registry empty', async () => {
    const { fake, store, dispose } = setup();
    fake.stubQuery('workflows.get', () => {
      throw new Error('no workflows');
    });

    await store.getState().actions.workflows.load();
    const { workflows } = store.getState();
    expect(workflows.status).toBe('error');
    expect(workflows.error).toBe('no workflows');
    expect(workflows.items).toEqual([]);
    dispose();
  });

  it('selectWorkflowsByName indexes by name (last-wins)', () => {
    const a1 = wf('a', '1');
    const b = wf('b', '2');
    const a3 = wf('a', '3');
    const map = selectWorkflowsByName([a1, b, a3]);
    expect(map.size).toBe(2);
    expect(map.get('a')).toEqual(a3);
    expect(map.get('b')).toEqual(b);
  });
});
