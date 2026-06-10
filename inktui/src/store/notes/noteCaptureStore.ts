/**
 * `noteCaptureStore` — the ESC-chord finite-state machine of the quick-capture draft (F9, salvaging
 * Textual's `NoteCaptureScreen` chord table, `app/tui/note_capture.py:109-247`).
 *
 * ## Why a store and not the dispatcher (the F9 "verify before porting" verdict)
 *
 * The capture screen is a {@link ../../input/modeStore.js Mode}: the root dispatcher captures input
 * for the active mode and routes a matched chord to its `onIntent` (and raw printable chars to its
 * `onUncaptured`). The dispatcher's matcher ({@link ../../input/keymap.js matchKeymap}) is pure and
 * *stateless* — it knows nothing of double-tap windows, two-key sequences, or idle timers. That is by
 * design: the FSM state lives **here**, and the mode's `onIntent`/`onUncaptured` closures call these
 * verbs. So all four Textual behaviors are expressible with **no new dispatcher primitive** —
 * identical to how `chatInputStore` holds the chat buffer behind the dispatcher's layer-2 handler and
 * `toastStore` owns its own `setTimeout`, not the dispatcher:
 *
 *  - **ESC double-tap (commit/cancel within {@link ESC_DOUBLE_TAP_S}s)** — every `escape` chord fires
 *    {@link NoteCaptureState.pressEscape}; the verb compares `now()` against the stored `escArmedAt`,
 *    exactly as Textual's `handle_escape_from_draft` compares `time.monotonic()`.
 *  - **ESC-then-`d` delete chord** — `d` is context-sensitive: it deletes the draft only while the
 *    blur timer is live (i.e. ESC was just pressed), else it is an ordinary character. The handler
 *    branches on {@link NoteCaptureState.blurTimerActive} — the store's sequence state — mirroring
 *    Textual's `if key == "d" and outer.blur_timer_active()`.
 *  - **Blur after {@link BLUR_DELAY_S}s idle** — a real `setTimeout` owned by this store (the
 *    toastStore precedent); on fire it moves focus draft→list and resets `escArmedAt`.
 *  - **Undo of the last `d` delete** — single-level: {@link NoteCaptureState.pressUndo} restores the
 *    snapshot the delete took, then drops it (Textual's `consume_undo_delete`).
 *
 * ## Faithful timing — two distinct constants
 *
 * Textual uses **two** windows (the plan summary collapsed them): the double-tap commit window is
 * 0.45s, but the blur fires at 0.35s. Because the blur clears `escArmedAt`, the second-ESC *commit*
 * path is effectively live only for the 0.35s before focus leaves for the list — after that the draft
 * is blurred and a further ESC dismisses via the list path (handled by the mode, not this FSM). Both
 * constants are preserved verbatim.
 *
 * Framework-agnostic vanilla Zustand (rule 4): no React, no Ink. The pure decision points take an
 * injectable `now` so the double-tap window tests deterministically (focusStore's "invariant as a
 * pure function" idiom); the blur uses a real timer driven by the repo's real-timer + `wait()` test
 * idiom (toastStore), with a tracked handle so {@link NoteCaptureState.reset} cancels it (no leak).
 */

import { createStore, type StoreApi } from 'zustand/vanilla';

/** ESC double-tap commit window, in milliseconds. Textual `ESC_DOUBLE_TAP_S = 0.45`. A second ESC
 * within this window of the first commits (cancel-without-submit); past it, the second ESC re-arms. */
export const ESC_DOUBLE_TAP_MS = 450;

/** Idle delay before the draft blurs focus to the recent-notes list, in milliseconds. Textual
 * `BLUR_DELAY_S = 0.35`. Distinct from the double-tap window — see the module doc. */
export const BLUR_DELAY_MS = 350;

/** Where focus sits within the capture screen. The draft is the home; after the blur timer fires
 * focus moves to the recent-notes list (Textual's `_blur_draft_to_list`). Held as data so a later
 * full capture surface reads it; the FSM transitions it. */
export type CaptureFocus = 'draft' | 'list';

/** The outcome of an {@link NoteCaptureState.pressEscape}, so the mode's `onIntent` knows what to do
 * after the verb mutates the FSM: `armed` = first tap (blur scheduled, stay open); `commit` = the
 * double-tap fired, the mode should dismiss without submitting (Textual `_finish(submitted=False)`). */
export type EscapeOutcome = 'armed' | 'commit';

/** The note-capture FSM state plus its transition verbs. */
export interface NoteCaptureState {
  /** The draft text. The capture surface's TextArea mirrors this; the FSM clears/restores it on the
   * delete and undo chords. (Ordinary character entry appends here, via the mode's `onUncaptured`.) */
  readonly draftText: string;
  /** The optional note title (item 3b). Empty → the backend auto/LLM-titles the note. Held here (next
   * to the draft) so it persists across cancel/reopen exactly as the draft does; `reset()` clears it. */
  readonly titleText: string;
  /** Where focus is. `draft` until the blur timer fires, then `list`. */
  readonly focus: CaptureFocus;
  /** Absolute `now()`-scale timestamp of the first ESC, or `null` when not armed. The second ESC
   * within {@link ESC_DOUBLE_TAP_MS} of this commits. Reset by a blur, a delete, or a commit. */
  readonly escArmedAt: number | null;
  /** True while the blur timer is pending — i.e. ESC was just pressed and focus has not yet left the
   * draft. This is the gate that makes `d` mean "delete the draft" rather than insert a character
   * (Textual's `blur_timer_active()`). */
  readonly blurTimerActive: boolean;
  /** The draft text the last `d` delete cleared, for a single-level undo, or `null` if nothing to
   * undo. Set by {@link pressDelete}, consumed (and cleared) by {@link pressUndo}. */
  readonly undoSnapshot: string | null;

  /** Set the draft text (the surface's editor pushes edits here; tests seed it). Does not touch FSM
   * arming — plain typing must not arm ESC. */
  setDraft(text: string): void;

  /** Set the title text (the title field's edits push here). Does not touch FSM arming. */
  setTitle(text: string): void;

  /**
   * Handle an `escape` keypress in the draft (Textual `handle_escape_from_draft`). If a prior ESC is
   * still within {@link ESC_DOUBLE_TAP_MS} of `now()`, this is the **double-tap**: cancel the blur,
   * disarm, and return `'commit'` (the mode dismisses without submitting). Otherwise arm `escArmedAt`
   * and schedule the blur timer, returning `'armed'`. `now` is injectable for deterministic tests.
   */
  pressEscape(now?: number): EscapeOutcome;

  /**
   * Handle the ESC-then-`d` delete chord (Textual `consume_escape_d_chord`). Only the caller's gate —
   * `d` is offered here **only while {@link blurTimerActive}** — makes this the chord; this verb
   * assumes that gate already passed. Snapshots the current draft for undo, clears the draft, cancels
   * the blur, and disarms. Returns the snapshot taken (handy for a caller/test).
   */
  pressDelete(): string;

  /**
   * Undo the last `d` delete (Textual `consume_undo_delete`). If a snapshot exists, restore it to the
   * draft, clear the snapshot, and return `true`; otherwise no-op and return `false` (so the mode can
   * let an unmatched `u` fall through to ordinary entry).
   */
  pressUndo(): boolean;

  /**
   * Reset the whole FSM (cancel a pending blur timer, drop arming/undo, return focus to the draft,
   * clear the draft). Called when the capture screen closes — the analogue of Textual `_finish`
   * cancelling the blur timer — and by tests between cases so the real timer never leaks across them.
   */
  reset(): void;
}

/** The note-capture store handle. Plain `StoreApi` — the FSM is self-contained (no cross-store
 * invariant like focus/mode have); the mode's `onIntent` is what wires it to the dispatcher. */
export type NoteCaptureStoreApi = StoreApi<NoteCaptureState>;

/**
 * Create a note-capture FSM store. Each call is an isolated instance with its own blur timer — unit
 * tests build a fresh one per case (toastStore/focusStore idiom); the app mounts the singleton built
 * from it. Starts at: empty draft, draft-focused, unarmed, no undo.
 */
export function createNoteCaptureStore(): NoteCaptureStoreApi {
  // The pending blur timer handle, closure-private so it is never store data (a timer handle is not
  // serializable state). `pressEscape` schedules it; `pressDelete`/`pressEscape`-commit/`reset`
  // cancel it; it self-clears on fire. `blurTimerActive` is the store-visible reflection of it.
  let blurTimer: ReturnType<typeof setTimeout> | null = null;

  const store = createStore<NoteCaptureState>()((set, get) => {
    /** Cancel any pending blur timer and forget its handle. Idempotent. */
    function cancelBlur(): void {
      if (blurTimer !== null) {
        clearTimeout(blurTimer);
        blurTimer = null;
      }
    }

    /** (Re)schedule the blur: after {@link BLUR_DELAY_MS} of no further ESC, move focus draft→list and
     * disarm. Mirrors Textual `_schedule_blur_to_table` → `_blur_draft_to_list`. */
    function scheduleBlur(): void {
      cancelBlur();
      const handle = setTimeout(() => {
        blurTimer = null;
        // Blur fires: focus leaves for the list and arming is dropped (Textual `_blur_draft_to_list`
        // sets `_draft_esc_armed_at = None`). After this a further ESC dismisses via the list path.
        set({ focus: 'list', escArmedAt: null, blurTimerActive: false });
      }, BLUR_DELAY_MS);
      handle.unref?.(); // don't keep the event loop alive for a blur timeout (Node-only; harmless if absent)
      blurTimer = handle;
      set({ blurTimerActive: true });
    }

    return {
      draftText: '',
      titleText: '',
      focus: 'draft',
      escArmedAt: null,
      blurTimerActive: false,
      undoSnapshot: null,

      setDraft(text) {
        set({ draftText: text });
      },

      setTitle(text) {
        set({ titleText: text });
      },

      pressEscape(now = Date.now()) {
        const { escArmedAt } = get();
        if (escArmedAt !== null && now - escArmedAt < ESC_DOUBLE_TAP_MS) {
          // Double-tap within the window → commit (dismiss without submit). Cancel blur, disarm.
          cancelBlur();
          set({ escArmedAt: null, blurTimerActive: false });
          return 'commit';
        }
        // First tap (or a stale prior tap past the window) → arm and (re)schedule the blur.
        set({ escArmedAt: now });
        scheduleBlur();
        return 'armed';
      },

      pressDelete() {
        const snapshot = get().draftText;
        cancelBlur();
        set({
          undoSnapshot: snapshot,
          draftText: '',
          escArmedAt: null,
          blurTimerActive: false,
        });
        return snapshot;
      },

      pressUndo() {
        const { undoSnapshot } = get();
        if (undoSnapshot === null) {
          return false;
        }
        set({ draftText: undoSnapshot, undoSnapshot: null });
        return true;
      },

      reset() {
        cancelBlur();
        set({
          draftText: '',
          titleText: '',
          focus: 'draft',
          escArmedAt: null,
          blurTimerActive: false,
          undoSnapshot: null,
        });
      },
    };
  });

  return store;
}

/**
 * The app-level singleton note-capture store — the instance the (future) capture mode imports and
 * wires into its `onIntent`/`onUncaptured`. Built from {@link createNoteCaptureStore}; tests use the
 * factory for isolation and call `reset()` in `beforeEach`.
 */
export const noteCaptureStore: NoteCaptureStoreApi = createNoteCaptureStore();
