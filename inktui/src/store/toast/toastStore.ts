/**
 * `toastStore` — transient toast feedback (F9, salvaging Textual's `ToastRack` / `self.notify()`).
 *
 * A toast is a short-lived, ambient, non-attention-grabbing message: "the send acked", "the image
 * finished". It is the *truth* of an async outcome, decoupled from the optimistic UI (Enter clears
 * the chat input instantly; the toast arrives only when the bus actually acks — see the conversations
 * `send` action and the plan's TODO-T). Textual got this for free from its framework; Ink has no
 * built-in, so this is the primitive.
 *
 * ## Two exports, two consumers (advisor's split)
 *
 *  - {@link createToastStore} — the factory, mirroring `createFocusStore`/`createModeStore` (the
 *    codebase's vanilla-Zustand ethos). Unit tests build a fresh, isolated store per test so they
 *    never share mutable global state or leak timers across cases.
 *  - {@link toastStore} — the app-level singleton instance, what production callers import: the
 *    conversations `send` action pushes the send toast here, and a later slice's `imageDraftStore`
 *    upload action will push the image done/failed toast here (left cleanly callable — it just
 *    `import { toastStore }` and calls `push`). Mounted once by the {@link ../../components/Toast.js
 *    Toast} component at the app root.
 *
 * ## Self-expiry — pure decision + a driving timer
 *
 * The expiry *decision* is the pure {@link selectLiveToasts}(toasts, now) filter (mirroring
 * focusStore's "invariant as a pure function"): a toast is live while `now <= expiresAt`. That is
 * what the component renders and what tests assert deterministically, with no timer race. The actual
 * *removal* is driven by a per-toast `setTimeout` (matching `submitCommand`'s existing real-timer
 * usage — the codebase uses real timers + a `tick()` helper, not fake timers). Timer handles are
 * tracked so {@link ToastState.clear} can cancel them, which is what keeps the singleton from leaking
 * timers across tests (and resets it between cases).
 */

import { createStore, type StoreApi } from 'zustand/vanilla';

/** Toast urgency. Deliberately minimal — `info` (the silent default) and `error` (the only one that
 * earns colour). Textual's `warning` is unused here, so it is not modelled (keep the union honest). */
export type ToastSeverity = 'info' | 'error';

/** Default time-to-live: short and ambient (~2–3s), so a toast is a glance, not a thing to dismiss. */
export const DEFAULT_TTL_MS = 2500;

/** How many toasts the component shows at once. The store keeps all live toasts; the *view* caps the
 * visible stack (newest-on-top) so a burst doesn't fill the screen. Lives here as the shared policy. */
export const MAX_VISIBLE_TOASTS = 3;

/** One toast, as plain data. `id` is monotonic (push order); `expiresAt` is an absolute epoch-ms
 * deadline so {@link selectLiveToasts} is a pure compare against `now` — no per-toast countdown state. */
export interface Toast {
  readonly id: number;
  readonly text: string;
  readonly severity: ToastSeverity;
  /** Absolute `Date.now()`-scale deadline; the toast is live while `now <= expiresAt`. */
  readonly expiresAt: number;
}

/** Options for {@link ToastState.push}. Both optional: `info` severity, default TTL. */
export interface PushOptions {
  readonly severity?: ToastSeverity;
  readonly ttlMs?: number;
}

/** The toast store's state: the live toasts plus the verbs. */
export interface ToastState {
  /** All currently-held toasts, oldest-first (push order). The component reverses + caps for display
   * (newest-on-top). A toast is removed when its timer fires; until then it sits here even if already
   * past `expiresAt` (the pure {@link selectLiveToasts} filter is the authority a reader trusts). */
  readonly toasts: readonly Toast[];
  /**
   * Push a toast. `text` is the message; `severity` defaults to `'info'`, `ttlMs` to
   * {@link DEFAULT_TTL_MS}. Returns the new toast's id (handy for a caller that wants to reason about
   * it; callers may ignore it). Schedules a self-expiry timer that drops this toast after `ttlMs`.
   */
  push(text: string, options?: PushOptions): number;
  /** Remove a toast by id now (its timer, if pending, is cancelled). Idempotent. */
  dismiss(id: number): void;
  /** Drop all toasts and cancel every pending expiry timer. The reset hook tests call in `beforeEach`
   * to keep the singleton from carrying toasts/timers between cases. */
  clear(): void;
}

/** The toast store handle. Plain `StoreApi` — no bound sibling store (unlike focus/mode), because a
 * toast is self-contained data with no cross-store invariant. */
export type ToastStoreApi = StoreApi<ToastState>;

/**
 * The live toasts at instant `now` — the expiry invariant as a pure function. A toast is live while
 * `now <= expiresAt`; expired ones are filtered out. This is what the component renders (so a toast
 * visually vanishes at its deadline even a hair before its removal timer fires) and what unit tests
 * assert against deterministically, passing an explicit `now` instead of racing real time.
 */
export function selectLiveToasts(toasts: readonly Toast[], now: number): readonly Toast[] {
  return toasts.filter((t) => now <= t.expiresAt);
}

/**
 * Create a toast store. Each call is an independent instance with its own timer table — unit tests
 * use this for isolation; production uses the shared {@link toastStore} singleton built from it.
 */
export function createToastStore(): ToastStoreApi {
  // Pending expiry timers keyed by toast id, so `dismiss`/`clear` can cancel them (no leaked timers
  // across tests, no removal firing after a manual dismiss). Closure-private — not store state.
  const timers = new Map<number, ReturnType<typeof setTimeout>>();
  let nextId = 1;

  const store = createStore<ToastState>()((set, get) => ({
    toasts: [],
    push(text, options = {}) {
      const id = nextId++;
      const ttlMs = options.ttlMs ?? DEFAULT_TTL_MS;
      const severity = options.severity ?? 'info';
      const toast: Toast = { id, text, severity, expiresAt: Date.now() + ttlMs };
      set((state) => ({ toasts: [...state.toasts, toast] }));
      const handle = setTimeout(() => {
        timers.delete(id);
        get().dismiss(id);
      }, ttlMs);
      // Don't keep the event loop alive just for a toast timeout (Node-only; harmless if absent).
      handle.unref?.();
      timers.set(id, handle);
      return id;
    },
    dismiss(id) {
      const handle = timers.get(id);
      if (handle !== undefined) {
        clearTimeout(handle);
        timers.delete(id);
      }
      set((state) => {
        const next = state.toasts.filter((t) => t.id !== id);
        // Keep array identity when nothing changed, so an idempotent dismiss doesn't churn renders.
        return next.length === state.toasts.length ? state : { toasts: next };
      });
    },
    clear() {
      for (const handle of timers.values()) {
        clearTimeout(handle);
      }
      timers.clear();
      set((state) => (state.toasts.length === 0 ? state : { toasts: [] }));
    },
  }));
  return store;
}

/**
 * The app-level singleton toast store — the instance production code imports. The conversations
 * `send` action pushes the send toast here on bus ack; the image-paste slice's `imageDraftStore`
 * will push the image done/failed toast here (it only needs `import { toastStore }` + `push`, left
 * cleanly callable). The {@link ../../components/Toast.js Toast} component subscribes to it at the
 * app root. Tests that exercise the singleton call `toastStore.getState().clear()` in `beforeEach`.
 */
export const toastStore: ToastStoreApi = createToastStore();
