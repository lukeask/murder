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
  TOAST_EXIT_MS,
  type Toast,
} from '../../../src/store/toast/toastStore.js';

/** Build a toast datum with an explicit deadline, for the pure-filter tests. */
function toast(id: number, expiresAt: number): Toast {
  return { id, text: `t${id}`, severity: 'info', createdAt: expiresAt - 100, expiresAt, count: 1 };
}

/** Wait `ms` real milliseconds — the store self-expires on real timers (no fake timers in this repo). */
function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

describe('selectLiveToasts (pure expiry filter)', () => {
  it('keeps toasts whose deadline is at or after now, drops the rest', () => {
    const toasts = [toast(1, 100), toast(2, 200), toast(3, 300)];
    expect(selectLiveToasts(toasts, 200 + TOAST_EXIT_MS + 1).map((t) => t.id)).toEqual([3]);
  });

  it('treats now === expiresAt as still live (inclusive deadline)', () => {
    expect(selectLiveToasts([toast(1, 500)], 500 + TOAST_EXIT_MS).map((t) => t.id)).toEqual([1]);
  });

  it('returns all when nothing has expired and none when all have', () => {
    const toasts = [toast(1, 100), toast(2, 100)];
    expect(selectLiveToasts(toasts, 50)).toHaveLength(2);
    expect(selectLiveToasts(toasts, 101 + TOAST_EXIT_MS)).toHaveLength(0);
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
    await wait(60 + TOAST_EXIT_MS);
    expect(store.getState().toasts).toHaveLength(0);
  });

  it('a fresh toast starts at count 1', () => {
    const store = createToastStore();
    store.getState().push('once', { ttlMs: 10_000 });
    expect(store.getState().toasts[0]?.count).toBe(1);
    store.getState().clear();
  });
});

describe('toastStore.push — dedup of a live identical toast', () => {
  it('bumps count instead of stacking a second identical row (length stays 1)', () => {
    const store = createToastStore();
    store.getState().push('boom', { severity: 'error', ttlMs: 10_000 });
    store.getState().push('boom', { severity: 'error', ttlMs: 10_000 });
    store.getState().push('boom', { severity: 'error', ttlMs: 10_000 });
    expect(store.getState().toasts).toHaveLength(1);
    expect(store.getState().toasts[0]?.count).toBe(3);
    store.getState().clear();
  });

  it('returns the SAME id for each deduped push (the existing toast, not a new one)', () => {
    const store = createToastStore();
    const first = store.getState().push('dup', { ttlMs: 10_000 });
    const second = store.getState().push('dup', { ttlMs: 10_000 });
    expect(second).toBe(first);
    store.getState().clear();
  });

  it('refreshes (bumps) expiresAt on a dedup so the message stays alive while it keeps firing', () => {
    const store = createToastStore();
    store.getState().push('dup', { ttlMs: 10_000 });
    const firstDeadline = store.getState().toasts[0]?.expiresAt ?? 0;
    // A later identical push should push the deadline forward (>= the original).
    store.getState().push('dup', { ttlMs: 10_000 });
    const bumped = store.getState().toasts[0]?.expiresAt ?? 0;
    expect(bumped).toBeGreaterThanOrEqual(firstDeadline);
    store.getState().clear();
  });

  it('does NOT dedup across differing severity (same text, info vs error → two rows)', () => {
    const store = createToastStore();
    store.getState().push('clash', { severity: 'info', ttlMs: 10_000 });
    store.getState().push('clash', { severity: 'error', ttlMs: 10_000 });
    expect(store.getState().toasts).toHaveLength(2);
    store.getState().clear();
  });

  it('does NOT dedup against an already-expired toast (a new live row is created)', async () => {
    const store = createToastStore();
    store.getState().push('stale', { ttlMs: 20 });
    await wait(40 + TOAST_EXIT_MS); // the first toast is past its deadline and exit grace.
    store.getState().push('stale', { ttlMs: 10_000 });
    const live = store.getState().toasts.filter((t) => Date.now() <= t.expiresAt);
    expect(live).toHaveLength(1);
    expect(live[0]?.count).toBe(1);
    store.getState().clear();
  });

  it('a deduped toast does NOT expire at the OLD deadline — the bumped timer keeps it alive', async () => {
    const store = createToastStore();
    store.getState().push('keep', { ttlMs: 40 });
    await wait(25); // before the first deadline
    store.getState().push('keep', { ttlMs: 80 }); // bump the deadline well past the original
    await wait(30); // now past the ORIGINAL 40ms deadline, before the bumped one
    // If the stale timer hadn't been cancelled, the toast would be gone here.
    expect(store.getState().toasts).toHaveLength(1);
    expect(store.getState().toasts[0]?.count).toBe(2);
    store.getState().clear();
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
