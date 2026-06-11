/**
 * `murderConfirmStore` — the pending state of the two-press murder chord (ctrl+m, ctrl+m/m).
 *
 * ## Why a tiny store and not a mode
 *
 * The murder confirm is deliberately NOT a {@link ../../input/modeStore.js mode}: the shell renders
 * the Overlay INSTEAD of the rails + Stage while any mode is up (App.tsx's body ternary), so a mode
 * would blank the whole layout for the confirm window — the opposite of the subtle "press m again"
 * cue this wants. Instead the pending target lives here, the cue is a plain toast, and the root
 * dispatcher consults the pending flag per-event (via injected handlers, staying pure) to claim the
 * confirming `m`/ctrl+m before the chat/panel layers see it. Any other key cancels the pending state
 * and keeps its normal meaning; an untouched pending state self-expires after {@link ARM_TTL_MS}.
 *
 * ## Two exports, two consumers (the toastStore split)
 *
 *  - {@link createMurderConfirmStore} — the factory; unit tests build an isolated instance so no
 *    pending state or expiry timer leaks across cases.
 *  - {@link murderConfirmStore} — the app singleton: the shell's murder handlers (App.tsx) and the
 *    CrowsPanel's cursor-row arm both write here, so the *armer* and the *confirmer* always agree on
 *    the target. Bound to the {@link toastStore} singleton for the "press m again" cue.
 */

import { createStore, type StoreApi } from 'zustand/vanilla';
import { type ToastStoreApi, toastStore } from '../toast/toastStore.js';

/** How long an armed murder stays confirmable. Short on purpose: the confirm press is the very next
 * keystroke or it isn't happening — a stale pending kill ambushing a later `m` would be a disaster. */
export const ARM_TTL_MS = 3000;

/** The armed target: the crow to kill and its display label (for the toasts). */
export interface MurderTarget {
  readonly agentId: string;
  readonly name: string;
}

/** The store's state: the armed target (or null) plus the verbs. */
export interface MurderConfirmState {
  /** The currently armed target, or `null` when nothing is pending. */
  readonly pending: MurderTarget | null;
  /**
   * Arm the confirm for `target`: set it pending, show the "press m again" toast, and schedule the
   * self-expiry. Re-arming (another ctrl+m on a different target before confirming) replaces the
   * pending target and restarts the clock.
   */
  arm(target: MurderTarget): void;
  /** Drop the pending target (confirm consumed it, another key cancelled it, or a test resets).
   * Cancels the expiry timer. Idempotent. */
  clear(): void;
}

export type MurderConfirmStoreApi = StoreApi<MurderConfirmState>;

/** Create an isolated murder-confirm store bound to a toast store (tests pass their own, and may
 * shorten `ttlMs` so the expiry is assertable on a real timer without a 3s wait). */
export function createMurderConfirmStore(
  toasts: ToastStoreApi,
  ttlMs: number = ARM_TTL_MS,
): MurderConfirmStoreApi {
  // The expiry timer handle is closure-private (not state) — exactly the toastStore timer pattern.
  let timer: ReturnType<typeof setTimeout> | null = null;
  const cancelTimer = (): void => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  };

  return createStore<MurderConfirmState>()((set, get) => ({
    pending: null,
    arm(target) {
      cancelTimer();
      set({ pending: target });
      toasts.getState().push(`press m again to murder ${target.name}`, { ttlMs });
      timer = setTimeout(() => {
        timer = null;
        get().clear();
      }, ttlMs);
      // Don't keep the event loop alive for an expiry (Node-only; harmless if absent).
      timer.unref?.();
    },
    clear() {
      cancelTimer();
      set((state) => (state.pending === null ? state : { pending: null }));
    },
  }));
}

/** The app-level singleton — what the shell handlers and the CrowsPanel import. */
export const murderConfirmStore: MurderConfirmStoreApi = createMurderConfirmStore(toastStore);
