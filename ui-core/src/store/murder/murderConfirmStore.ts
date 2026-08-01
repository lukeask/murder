/**
 * `murderConfirmStore` — the pending state of the two-press murder chord (ctrl+m, ctrl+m/m).
 *
 * ## Why a tiny store and not a mode
 *
 * The murder confirm is deliberately NOT a {@link ../../input/modeStore.js mode}: modal overlays
 * capture the shell layout while any mode is up, so a mode would obscure the normal pane context for
 * the confirm window — the opposite of the subtle "press m again" cue this wants. Instead the
 * pending target lives here, the cue is a plain toast, and the root
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

/** A two-press confirm store's state, generic over the armed target shape. */
export interface ConfirmState<T> {
  /** The currently armed target, or `null` when nothing is pending. */
  readonly pending: T | null;
  /**
   * Arm the confirm for `target`: set it pending, show the "press again" toast, and schedule the
   * self-expiry. Re-arming (another arm press on a different target before confirming) replaces the
   * pending target and restarts the clock.
   */
  arm(target: T): void;
  /** Drop the pending target (confirm consumed it, another key cancelled it, or a test resets).
   * Cancels the expiry timer. Idempotent. */
  clear(): void;
}

/** The store's state: the armed target (or null) plus the verbs. */
export type MurderConfirmState = ConfirmState<MurderTarget>;

export type MurderConfirmStoreApi = StoreApi<MurderConfirmState>;

/**
 * Create an isolated two-press confirm store bound to a toast store. Generic over the target shape;
 * `message` renders the "press <key> again to …" cue for the arm toast. Tests pass their own toast
 * store and may shorten `ttlMs` so the expiry is assertable on a real timer without a 3s wait.
 */
export function createConfirmStore<T>(
  toasts: ToastStoreApi,
  message: (target: T) => string,
  ttlMs: number = ARM_TTL_MS,
): StoreApi<ConfirmState<T>> {
  // The expiry timer handle is closure-private (not state) — exactly the toastStore timer pattern.
  let timer: ReturnType<typeof setTimeout> | null = null;
  const cancelTimer = (): void => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  };

  return createStore<ConfirmState<T>>()((set, get) => ({
    pending: null,
    arm(target) {
      cancelTimer();
      set({ pending: target });
      toasts.getState().push(message(target), { ttlMs });
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

/** Create an isolated murder-confirm store bound to a toast store (tests pass their own, and may
 * shorten `ttlMs` so the expiry is assertable on a real timer without a 3s wait). */
export function createMurderConfirmStore(
  toasts: ToastStoreApi,
  ttlMs: number = ARM_TTL_MS,
): MurderConfirmStoreApi {
  return createConfirmStore<MurderTarget>(
    toasts,
    (target) => `press m again to murder ${target.name}`,
    ttlMs,
  );
}

/** The app-level singleton — what the shell handlers and the CrowsPanel import. */
export const murderConfirmStore: MurderConfirmStoreApi = createMurderConfirmStore(toastStore);

/** The armed reset target: the ticket to re-queue and the cursor row's display label. */
export interface ResetTarget {
  readonly ticketId: string;
  readonly name: string;
}

/**
 * The crow-reset confirm singleton (`x`, `x` in the crows panel — Objective 1 of the lifecycle-
 * robustness plan). Unlike the murder confirm, BOTH presses land in the CrowsPanel keymap (the
 * plain `x` chord only fires while that panel is focused), so no dispatcher pending-check is
 * needed: the panel re-derives the cursor row on the second press and confirms only when it still
 * matches the armed ticket. The self-expiry guards the stale-arm case.
 */
export const resetConfirmStore: StoreApi<ConfirmState<ResetTarget>> =
  createConfirmStore<ResetTarget>(toastStore, (target) => `press x again to reset ${target.name}`);
