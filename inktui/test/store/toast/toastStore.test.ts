/**
 * `toastStore` tests — the F9 transient-feedback primitive.
 *
 * Two layers, matching the store's two-export design:
 *  - the pure {@link selectLiveToasts} expiry filter, asserted deterministically with an explicit
 *    `now` (no timer race — mirrors how focusStore tests the re-home invariant as a pure function);
 *  - the `push`/`dismiss`/`clear` verbs over a *fresh factory instance* (no shared global state), with
 *    the real per-toast `setTimeout` self-expiry driven by the codebase's real-timer + `tick` idiom.
 */

import { describe, expect, it } from 'vitest';
import {
  createToastStore,
  DEFAULT_TTL_MS,
  selectLiveToasts,
  type Toast,
} from '../../../src/store/toast/toastStore.js';

/** Build a toast datum with an explicit deadline, for the pure-filter tests. */
function toast(id: number, expiresAt: number): Toast {
  return { id, text: `t${id}`, severity: 'info', expiresAt };
}

/** Wait `ms` real milliseconds — the store self-expires on real timers (no fake timers in this repo). */
function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

describe('selectLiveToasts (pure expiry filter)', () => {
  it('keeps toasts whose deadline is at or after now, drops the rest', () => {
    const toasts = [toast(1, 100), toast(2, 200), toast(3, 300)];
    expect(selectLiveToasts(toasts, 200).map((t) => t.id)).toEqual([2, 3]);
  });

  it('treats now === expiresAt as still live (inclusive deadline)', () => {
    expect(selectLiveToasts([toast(1, 500)], 500).map((t) => t.id)).toEqual([1]);
  });

  it('returns all when nothing has expired and none when all have', () => {
    const toasts = [toast(1, 100), toast(2, 100)];
    expect(selectLiveToasts(toasts, 50)).toHaveLength(2);
    expect(selectLiveToasts(toasts, 101)).toHaveLength(0);
  });
});

describe('toastStore.push', () => {
  it('defaults to info severity and the default TTL', () => {
    const store = createToastStore();
    const before = Date.now();
    store.getState().push('hello');
    const [t] = store.getState().toasts;
    expect(t?.severity).toBe('info');
    expect(t?.text).toBe('hello');
    expect(t?.expiresAt).toBeGreaterThanOrEqual(before + DEFAULT_TTL_MS);
    store.getState().clear();
  });

  it('honours an explicit error severity and ttl', () => {
    const store = createToastStore();
    const before = Date.now();
    store.getState().push('boom', { severity: 'error', ttlMs: 10_000 });
    const [t] = store.getState().toasts;
    expect(t?.severity).toBe('error');
    expect(t?.expiresAt).toBeGreaterThanOrEqual(before + 10_000);
    store.getState().clear();
  });

  it('stacks toasts oldest-first with monotonic ids', () => {
    const store = createToastStore();
    store.getState().push('a', { ttlMs: 10_000 });
    store.getState().push('b', { ttlMs: 10_000 });
    expect(store.getState().toasts.map((t) => t.text)).toEqual(['a', 'b']);
    const ids = store.getState().toasts.map((t) => t.id);
    expect(ids[1]).toBeGreaterThan(ids[0] as number);
    store.getState().clear();
  });

  it('self-expires: the toast is gone after its ttl elapses', async () => {
    const store = createToastStore();
    store.getState().push('blip', { ttlMs: 30 });
    expect(store.getState().toasts).toHaveLength(1);
    await wait(60);
    expect(store.getState().toasts).toHaveLength(0);
  });
});

describe('toastStore.dismiss / clear', () => {
  it('dismiss removes one toast by id and cancels its expiry timer', async () => {
    const store = createToastStore();
    const id = store.getState().push('a', { ttlMs: 10_000 });
    store.getState().push('b', { ttlMs: 10_000 });
    store.getState().dismiss(id);
    expect(store.getState().toasts.map((t) => t.text)).toEqual(['b']);
    // Dismissing again is idempotent (no throw, no change).
    store.getState().dismiss(id);
    expect(store.getState().toasts).toHaveLength(1);
    store.getState().clear();
  });

  it('clear drops every toast and cancels timers (no late removal fires)', async () => {
    const store = createToastStore();
    store.getState().push('a', { ttlMs: 20 });
    store.getState().push('b', { ttlMs: 20 });
    store.getState().clear();
    expect(store.getState().toasts).toHaveLength(0);
    // After clear, a new push past the old deadline must survive — proving the old timers were
    // cancelled and don't reach in to remove anything.
    await wait(40);
    store.getState().push('c', { ttlMs: 10_000 });
    expect(store.getState().toasts.map((t) => t.text)).toEqual(['c']);
    store.getState().clear();
  });
});
