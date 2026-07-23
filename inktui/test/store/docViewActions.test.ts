/**
 * Doc-view actions tests — the on-demand doc fetch (rule 3).
 *  - `open(kind, name)` fires the per-kind `state.{plan,note,report}_display` RPC and fills the
 *    slice with the body from the `DisplaySnapshot` reply (`{ name, markdown }`).
 *  - `close()` resets the slice to closed.
 *  - a fetch rejection lands in `error` without crashing.
 */

import { describe, expect, it } from 'vitest';
import { FakeApplicationClient } from '../../src/application/FakeApplicationClient.js';
import { createAppStore } from '../../src/store/store.js';

function setup() {
  const fake = new FakeApplicationClient();
  fake.stubQuery('roster.get', { invalidation_key: 'iv', sessions: [] });
  // F2: doc bodies come from per-kind display RPCs returning a DisplaySnapshot ({ name, markdown }).
  fake.stubQuery('plan.get', { name: 'p', markdown: '# Title\nbody' });
  fake.stubQuery('note.get', { name: 'my-note', markdown: '# Title\nbody' });
  fake.stubQuery('report.get', { name: 'r', markdown: '# Title\nbody' });
  const { store, dispose } = createAppStore(fake);
  return { fake, store, dispose };
}

describe('docView actions', () => {
  it('open() fires the per-kind display RPC and fills the slice with kind/name/body', async () => {
    const { fake, store, dispose } = setup();
    await store.getState().actions.docView.open('note', 'my-note');

    const getCalls = fake.queryCalls.filter((c) => c.name === 'note.get');
    expect(getCalls.length).toBe(1);
    expect(getCalls[0]?.params).toEqual({ name: 'my-note' });

    const dv = store.getState().docView;
    expect(dv.open).toEqual({ kind: 'note', name: 'my-note' });
    expect(dv.body).toBe('# Title\nbody');
    expect(dv.status).toBe('ready');
    dispose();
  });

  it('close() resets the slice to closed', async () => {
    const { store, dispose } = setup();
    await store.getState().actions.docView.open('plan', 'p');
    store.getState().actions.docView.close();
    const dv = store.getState().docView;
    expect(dv.open).toBeNull();
    expect(dv.body).toBeNull();
    expect(dv.status).toBe('idle');
    dispose();
  });

  it('a fetch rejection sets status=error', async () => {
    const { fake, store, dispose } = setup();
    fake.stubQuery('report.get', () => {
      throw new Error('not found');
    });
    await store.getState().actions.docView.open('report', 'r');
    const dv = store.getState().docView;
    expect(dv.status).toBe('error');
    expect(dv.error).toBe('not found');
    // still records the open doc so the surface can show which doc failed.
    expect(dv.open).toEqual({ kind: 'report', name: 'r' });
    dispose();
  });
});
