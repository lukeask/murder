/**
 * `noteCaptureMode` — the dispatcher wiring of the note-capture ESC-chord FSM (F9). This is the proof
 * that the {@link ./noteCaptureStore.js noteCaptureStore} FSM is expressible through the existing root
 * dispatcher with **no new primitive**: it builds a {@link ../../input/modeStore.js Mode} whose
 * `keymap`/`onIntent`/`onUncaptured` route captured keys to the store's verbs, exactly as
 * {@link ../../components/TicketEditorMode.js ticketEditorMode} routes the vim editor's keys (the C12
 * reference) — including the `d`-as-two-key-sequence trick via closure/store state, not a dispatcher
 * feature.
 *
 * ## What each captured key does (the FSM table → dispatch)
 *
 *  - **`escape`** — a declared chord → `onIntent('escape')` → {@link NoteCaptureState.pressEscape}.
 *    The double-tap window lives in the store; the dispatcher just delivers each press. On a `'commit'`
 *    outcome the mode dismisses (`modes.exit` then the caller's `onCancel`); on `'armed'` it stays.
 *  - **`d`** — context-sensitive, so it is NOT a static chord; it flows through `onUncaptured`. It is
 *    the delete chord **only while {@link NoteCaptureState.blurTimerActive}** (i.e. ESC just armed it);
 *    otherwise it is an ordinary character appended to the draft. This is the ticketEditor `pendingD`
 *    pattern: a two-key sequence expressed in store/closure state, gated in the handler.
 *  - **`u`** — also context-sensitive: it undoes the last delete **only if there is a snapshot**
 *    ({@link NoteCaptureState.pressUndo} returns `true`); with nothing to undo it is an ordinary `u`.
 *    So it too lives in `onUncaptured`.
 *  - **any other printable char** — appended to the draft (the capture surface's text entry).
 *  - **`return`** — a declared chord → submit the draft (the caller's `onSubmit`), if non-empty.
 *
 * `presentation: 'modal'` matches Textual's `ModalScreen`. `render: () => null` is deliberate: this
 * slice ports only the **FSM + its dispatch wiring**, not the recent-table / preview / draft-TextArea
 * shell (which would drag in image-paste — out of this slice's scope). A later chunk supplies the
 * surface component and swaps `render`; the input contract proven here is unchanged.
 *
 * Framework-light: imports only the mode/keymap types and the FSM store (no React) — the `render`
 * thunk is `() => null`, so even the `ReactNode` return is trivially satisfied.
 */

import type { Key } from 'ink';
import type { Mode, ModeStoreApi } from '../../input/modeStore.js';
import type { NoteCaptureStoreApi } from './noteCaptureStore.js';

// Bring the dispatcher's `onUncaptured` augmentation of `Mode` into scope (declared in dispatcher.ts).
import '../../input/dispatcher.js';

/** The note-capture mode's declared-chord intent union. `d`/`u`/printable are NOT here — they are
 * context-sensitive and flow through `onUncaptured` (see the module doc). */
type NoteCaptureIntent = 'escape' | 'submit';

/** Stable mode id so a re-enter is idempotent (the modeStore pattern). */
export const NOTE_CAPTURE_MODE_ID = 'note-capture';

/** What the caller supplies when opening the capture screen. */
export interface NoteCaptureModeOptions {
  /** Run when the draft is submitted (Enter on non-empty text). The action layer does the bus call. */
  readonly onSubmit: (draft: string) => void;
  /** Run when the capture is cancelled (the ESC double-tap commit). */
  readonly onCancel: () => void;
}

/**
 * Build the note-capture {@link Mode}, wiring the dispatcher to the {@link NoteCaptureStoreApi} FSM.
 * `modes` is for self-dismiss; `store` is the FSM whose verbs the handlers call. The returned mode is
 * plain data — the dispatcher's layer 0 captures all keys for it and routes them here.
 */
export function noteCaptureMode(
  modes: ModeStoreApi,
  store: NoteCaptureStoreApi,
  options: NoteCaptureModeOptions,
): Mode<NoteCaptureIntent> {
  const id = NOTE_CAPTURE_MODE_ID;

  /** Dismiss the mode then run the caller's handler (the modeStore "exit-then-act" contract, so a
   * follow-on action that opens another mode stacks correctly). */
  function dismiss(after: () => void): void {
    store.getState().reset();
    modes.getState().exit(id);
    after();
  }

  return {
    id,
    presentation: 'modal',
    // No passThrough: the capture modal captures everything (Textual's ModalScreen behavior).
    keymap: [
      // ESC: every press fires here; the store decides arm-vs-commit (the double-tap FSM).
      {
        chord: { key: { escape: true } },
        intent: 'escape',
        description: 'esc·esc close / esc d clear',
      },
      // Enter submits the draft (Shift+Enter newline is the surface's job; not part of the FSM).
      { chord: { key: { return: true } }, intent: 'submit', description: 'save in background' },
    ],
    onIntent(intent) {
      switch (intent) {
        case 'escape': {
          const outcome = store.getState().pressEscape();
          if (outcome === 'commit') {
            // ESC double-tap → cancel-without-submit (Textual `_finish(submitted=False)`).
            dismiss(options.onCancel);
          }
          // 'armed' → stay open; the blur timer is now ticking (handled inside the store).
          return;
        }
        case 'submit': {
          // Snapshot the draft BEFORE dismiss (which resets the store) so onSubmit sees the text.
          const draft = store.getState().draftText;
          if (draft.trim() !== '') {
            dismiss(() => options.onSubmit(draft));
          }
          return;
        }
        default:
          return intent satisfies never;
      }
    },
    // onUncaptured: the context-sensitive keys + ordinary text entry. The dispatcher calls this when
    // the declared keymap has no match (the C12 hook, as ticketEditor uses it).
    onUncaptured(input: string, key: Key): boolean {
      // Ignore modified/special non-character events — not ours; let the dispatcher swallow them.
      if (input.length === 0 || key.ctrl || key.meta || key.escape || key.return) {
        return false;
      }
      const state = store.getState();
      // `d` is the delete chord ONLY while the blur timer is live (ESC just armed it) — else literal.
      if (input === 'd' && state.blurTimerActive) {
        state.pressDelete();
        return true;
      }
      // `u` undoes the last delete ONLY if there is a snapshot — else it is a literal `u`.
      if (input === 'u' && state.pressUndo()) {
        return true;
      }
      // Ordinary character: append to the draft (plain text entry never arms ESC).
      state.setDraft(state.draftText + input);
      return true;
    },
    // Render is intentionally empty — this slice ports the FSM + dispatch wiring, not the modal shell.
    render: () => null,
  };
}
