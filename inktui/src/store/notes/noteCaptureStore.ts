/**
 * `noteCaptureStore` — the capture state machine of the quick-capture draft.
 *
 * ## Why a store and not the dispatcher (the F9 "verify before porting" verdict)
 *
 * The capture screen is a {@link ../../input/modeStore.js Mode}: the root dispatcher captures input
 * for the active mode and routes a matched chord to its `onIntent` (and raw printable chars to its
 * `onUncaptured`). The dispatcher's matcher ({@link ../../input/keymap.js matchKeymap}) is pure and
 * stateless; draft lifecycle lives here.
 *
 *  - **ESC cancel** — every `escape` chord fires {@link NoteCaptureState.pressEscape}; the verb
 *    returns `commit` immediately so the mode dismisses without submitting.
 *  - **Delete + undo** — {@link NoteCaptureState.pressDelete} snapshots and clears the draft, and
 *    {@link NoteCaptureState.pressUndo} restores that single snapshot.
 *
 * Framework-agnostic vanilla Zustand (rule 4): no React, no Ink.
 */

import { createStore, type StoreApi } from 'zustand/vanilla';

/** The outcome of an {@link NoteCaptureState.pressEscape}, so the mode's `onIntent` knows what to do
 * after the verb mutates the FSM: `commit` = the mode should dismiss without submitting. */
export type EscapeOutcome = 'commit';

/** The note-capture FSM state plus its transition verbs. */
export interface NoteCaptureState {
  /** The draft text. The capture surface's TextArea mirrors this; the FSM clears/restores it on the
   * delete and undo chords. (Ordinary character entry appends here, via the mode's `onUncaptured`.) */
  readonly draftText: string;
  /** The optional note title (item 3b). Empty → the backend auto/LLM-titles the note. Held here (next
   * to the draft) so it persists across cancel/reopen exactly as the draft does; `reset()` clears it. */
  readonly titleText: string;
  /** The draft text the last `d` delete cleared, for a single-level undo, or `null` if nothing to
   * undo. Set by {@link pressDelete}, consumed (and cleared) by {@link pressUndo}. */
  readonly undoSnapshot: string | null;

  /** Set the draft text (the surface's editor pushes edits here; tests seed it). */
  setDraft(text: string): void;

  /** Set the title text (the title field's edits push here). */
  setTitle(text: string): void;

  /**
   * Handle an `escape` keypress in the draft. Escape cancels immediately and returns `'commit'` so the
   * mode dismisses without submitting.
   */
  pressEscape(): EscapeOutcome;

  /**
   * Snapshot the current draft for undo and clear the draft. Returns the snapshot taken.
   */
  pressDelete(): string;

  /**
   * Undo the last `d` delete (Textual `consume_undo_delete`). If a snapshot exists, restore it to the
   * draft, clear the snapshot, and return `true`; otherwise no-op and return `false` (so the mode can
   * let an unmatched `u` fall through to ordinary entry).
   */
  pressUndo(): boolean;

  /**
   * Reset the whole FSM: drop undo, clear the draft and title. Called after submit and by tests.
   */
  reset(): void;
}

/** The note-capture store handle. Plain `StoreApi` — the FSM is self-contained (no cross-store
 * invariant like focus/mode have); the mode's `onIntent` is what wires it to the dispatcher. */
export type NoteCaptureStoreApi = StoreApi<NoteCaptureState>;

/**
 * Create a note-capture FSM store. Each call is an isolated instance; tests build a fresh one per
 * case and the app mounts the singleton built from it. Starts at: empty draft/title, no undo.
 */
export function createNoteCaptureStore(): NoteCaptureStoreApi {
  const store = createStore<NoteCaptureState>()((set, get) => ({
    draftText: '',
    titleText: '',
    undoSnapshot: null,

    setDraft(text) {
      set({ draftText: text });
    },

    setTitle(text) {
      set({ titleText: text });
    },

    pressEscape() {
      return 'commit';
    },

    pressDelete() {
      const snapshot = get().draftText;
      set({
        undoSnapshot: snapshot,
        draftText: '',
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
      set({
        draftText: '',
        titleText: '',
        undoSnapshot: null,
      });
    },
  }));

  return store;
}

/**
 * The app-level singleton note-capture store — the instance the (future) capture mode imports and
 * wires into its `onIntent`/`onUncaptured`. Built from {@link createNoteCaptureStore}; tests use the
 * factory for isolation and call `reset()` in `beforeEach`.
 */
export const noteCaptureStore: NoteCaptureStoreApi = createNoteCaptureStore();
