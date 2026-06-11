/**
 * murderConfirmStore tests — the pending state of the two-press murder chord.
 *
 * Isolated factory instances per case (the createToastStore pattern): no pending state or expiry
 * timer leaks across tests. Expiry uses a real timer with a shortened ttl (the codebase's real-timer
 * + tick idiom, not fake timers).
 */

import { describe, expect, it } from 'vitest';
import { createMurderConfirmStore } from '../../../src/store/murder/murderConfirmStore.js';
import { createToastStore, selectLiveToasts } from '../../../src/store/toast/toastStore.js';

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

describe('murderConfirmStore', () => {
  it('arm sets the pending target and pushes the "press m again" toast', () => {
    const toasts = createToastStore();
    const store = createMurderConfirmStore(toasts);

    store.getState().arm({ agentId: 'crow-1', name: 'scout' });

    expect(store.getState().pending).toEqual({ agentId: 'crow-1', name: 'scout' });
    const live = selectLiveToasts(toasts.getState().toasts, Date.now());
    expect(live.map((t) => t.text)).toContain('press m again to murder scout');
    store.getState().clear();
    toasts.getState().clear();
  });

  it('re-arming replaces the pending target', () => {
    const toasts = createToastStore();
    const store = createMurderConfirmStore(toasts);

    store.getState().arm({ agentId: 'crow-1', name: 'scout' });
    store.getState().arm({ agentId: 'crow-2', name: 'mason' });

    expect(store.getState().pending?.agentId).toBe('crow-2');
    store.getState().clear();
    toasts.getState().clear();
  });

  it('clear drops the pending target and is idempotent', () => {
    const toasts = createToastStore();
    const store = createMurderConfirmStore(toasts);

    store.getState().arm({ agentId: 'crow-1', name: 'scout' });
    store.getState().clear();
    expect(store.getState().pending).toBeNull();
    store.getState().clear(); // idempotent — no throw, no change
    expect(store.getState().pending).toBeNull();
    toasts.getState().clear();
  });

  it('an untouched pending target self-expires after the ttl', async () => {
    const toasts = createToastStore();
    const store = createMurderConfirmStore(toasts, 30);

    store.getState().arm({ agentId: 'crow-1', name: 'scout' });
    expect(store.getState().pending).not.toBeNull();

    await wait(60);
    expect(store.getState().pending).toBeNull();
    toasts.getState().clear();
  });

  it('clear cancels the expiry timer (a re-arm after clear is not clobbered by the old timer)', async () => {
    const toasts = createToastStore();
    const store = createMurderConfirmStore(toasts, 40);

    store.getState().arm({ agentId: 'crow-1', name: 'scout' });
    store.getState().clear();
    // Re-arm inside the first timer's original window: if clear leaked the timer, this pending
    // target would be wiped at the first deadline (~40ms in).
    await wait(20);
    store.getState().arm({ agentId: 'crow-2', name: 'mason' });
    await wait(30); // past the first deadline, inside the second window
    expect(store.getState().pending?.agentId).toBe('crow-2');
    store.getState().clear();
    toasts.getState().clear();
  });
});
