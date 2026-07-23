/**
 * `imageDraftStore` tests — the F9 client-side pasted-image ledger.
 *
 * Covers the keystone behaviours from plan TODO-F:
 *  - paste mints a stem synchronously and records `uploading` (the label→file binding is known *now*);
 *  - the FIFO queue serializes uploads (one `image.upload` in flight at a time, paste order preserved);
 *  - resolve flips `uploading → done` (path filled) or `→ failed`, each pushing the right toast;
 *  - a draft dropped mid-flight has its upload result discarded and its toast suppressed.
 *
 * Drives a {@link FakeApplicationClient} + a fresh isolated {@link createToastStore} so no global state leaks.
 */

import { describe, expect, it } from 'vitest';
import { FakeApplicationClient } from '../../../src/application/FakeApplicationClient.js';
import { createImageDraftStore } from '../../../src/store/imageDraft/imageDraftStore.js';
import { createToastStore } from '../../../src/store/toast/toastStore.js';

/** Microtask flush — the FIFO worker awaits the (resolved) RPC, so a couple of ticks settle it. */
async function flush(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

describe('imageDraftStore.paste', () => {
  it('mints a stem synchronously and records the draft as uploading before any await', () => {
    const bus = new FakeApplicationClient();
    bus.stubCommand('image.upload', { ok: true, path: '/p.png' });
    const store = createImageDraftStore(bus, createToastStore());

    const id = store.getState().paste(Buffer.from('x'), 'png');

    // Synchronous: the draft exists and is uploading the instant paste returns (no await needed).
    const draft = store.getState().drafts[id];
    expect(draft?.status).toBe('uploading');
    expect(draft?.stem).toBe(id);
    expect(draft?.ext).toBe('png');
  });

  it('passes the minted stem as `name` to image.upload and fills the path on done', async () => {
    const bus = new FakeApplicationClient();
    bus.stubCommand('image.upload', (params) => ({ ok: true, path: `/img/${params['name']}.png` }));
    const toasts = createToastStore();
    const store = createImageDraftStore(bus, toasts);

    const id = store.getState().paste(Buffer.from('abc'), 'png');
    await flush();

    const draft = store.getState().drafts[id];
    expect(draft?.status).toBe('done');
    expect(draft?.path).toBe(`/img/${id}.png`);
    expect(bus.commandCalls[0]?.params).toMatchObject({ name: id, ext: 'png' });
    // base64 of "abc"
    expect((bus.commandCalls[0]?.params as { bytes: string }).bytes).toBe(
      Buffer.from('abc').toString('base64'),
    );
    expect(toasts.getState().toasts.some((t) => t.severity === 'info')).toBe(true);
  });

  it('flips to failed and pushes an error toast when the server returns !ok', async () => {
    const bus = new FakeApplicationClient();
    bus.stubCommand('image.upload', { ok: false, error: 'disk full' });
    const toasts = createToastStore();
    const store = createImageDraftStore(bus, toasts);

    const id = store.getState().paste(Buffer.from('x'), 'png');
    await flush();

    expect(store.getState().drafts[id]?.status).toBe('failed');
    expect(toasts.getState().toasts.some((t) => t.severity === 'error')).toBe(true);
  });

  it('flips to failed and pushes an error toast when the RPC rejects', async () => {
    const bus = new FakeApplicationClient();
    bus.stubCommand('image.upload', () => {
      throw new Error('socket closed');
    });
    const toasts = createToastStore();
    const store = createImageDraftStore(bus, toasts);

    const id = store.getState().paste(Buffer.from('x'), 'png');
    await flush();

    expect(store.getState().drafts[id]?.status).toBe('failed');
    expect(toasts.getState().toasts.some((t) => t.severity === 'error')).toBe(true);
  });

  it('serializes uploads FIFO — one in flight at a time, in paste order', async () => {
    const bus = new FakeApplicationClient();
    let inFlight = 0;
    let maxConcurrent = 0;
    const order: string[] = [];
    // A handler that resolves on the next microtask, recording concurrency + order.
    bus.stubCommand('image.upload', async (params) => {
      inFlight += 1;
      maxConcurrent = Math.max(maxConcurrent, inFlight);
      await Promise.resolve();
      order.push(String(params['name']));
      inFlight -= 1;
      return { ok: true, path: `/p/${params['name']}` };
    });
    const store = createImageDraftStore(bus, createToastStore());

    const a = store.getState().paste(Buffer.from('1'), 'png');
    const b = store.getState().paste(Buffer.from('2'), 'png');
    const c = store.getState().paste(Buffer.from('3'), 'png');
    // Let all three drain.
    for (let i = 0; i < 10; i++) {
      await flush();
    }

    expect(maxConcurrent).toBe(1);
    expect(order).toEqual([a, b, c]);
    expect(store.getState().drafts[a]?.status).toBe('done');
    expect(store.getState().drafts[c]?.status).toBe('done');
  });

  it('drops a draft mid-flight: discards the upload result and suppresses its toast', async () => {
    const bus = new FakeApplicationClient();
    bus.stubCommand('image.upload', async (params) => {
      await Promise.resolve();
      return { ok: true, path: `/p/${params['name']}` };
    });
    const toasts = createToastStore();
    const store = createImageDraftStore(bus, toasts);

    const id = store.getState().paste(Buffer.from('x'), 'png');
    // Delete before the upload resolves.
    store.getState().drop(id);
    for (let i = 0; i < 5; i++) {
      await flush();
    }

    // Gone from the map; no done-toast pushed for the discarded result.
    expect(store.getState().drafts[id]).toBeUndefined();
    expect(toasts.getState().toasts).toHaveLength(0);
  });

  it('pathsById returns only done drafts (uploading/failed excluded)', async () => {
    const bus = new FakeApplicationClient();
    // First paste succeeds; re-stub so the second fails — exercises both branches into pathsById.
    bus.stubCommand('image.upload', (params) => ({ ok: true, path: `/p/${params['name']}` }));
    const store = createImageDraftStore(bus, createToastStore());
    const okId = store.getState().paste(Buffer.from('y'), 'png');
    for (let i = 0; i < 5; i++) {
      await flush();
    }
    bus.stubCommand('image.upload', { ok: false, error: 'no' });
    const badId = store.getState().paste(Buffer.from('z'), 'png');
    for (let i = 0; i < 5; i++) {
      await flush();
    }

    const map = store.getState().pathsById();
    expect(map.get(okId)).toBe(`/p/${okId}`);
    expect(map.has(badId)).toBe(false);
  });
});
