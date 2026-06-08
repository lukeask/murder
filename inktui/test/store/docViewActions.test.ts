/**
 * Doc-view actions tests — the on-demand doc fetch (rule 3).
 *  - `open(kind, name)` fires `doc.get` and fills the slice with the body.
 *  - `close()` resets the slice to closed.
 *  - a fetch rejection lands in `error` without crashing.
 */

import { describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { createAppStore } from '../../src/store/store.js';

function setup() {
  const fake = new FakeBusClient();
  fake.stubRpc('crow.get_snapshot', { invalidation_key: 'iv', sessions: [] });
  fake.stubRpc('doc.get', { body: '# Title\nbody' });
  const { store, dispose } = createAppStore(fake);
  return { fake, store, dispose };
}

describe('docView actions', () => {
  it('open() fires doc.get and fills the slice with kind/name/body', async () => {
    const { fake, store, dispose } = setup();
    await store.getState().actions.docView.open('note', 'my-note');

    const getCalls = fake.rpcCalls.filter((c) => c.method === 'doc.get');
    expect(getCalls.length).toBe(1);
    expect(getCalls[0]?.params).toEqual({ kind: 'note', name: 'my-note' });

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
    fake.stubRpc('doc.get', () => {
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
