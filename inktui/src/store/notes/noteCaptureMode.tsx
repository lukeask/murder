/**
 * `noteCaptureMode` — the dispatcher wiring + surface of the note-capture ESC-chord FSM (item 10,
 * salvaging Textual's `NoteCaptureScreen`). It builds a {@link ../../input/modeStore.js Mode} whose
 * `keymap`/`onIntent`/`onUncaptured` route captured keys to the {@link ./noteCaptureStore.js
 * noteCaptureStore} FSM, with **no new dispatcher primitive** — the same C12 pattern the spawn wizard
 * and ticket editor use.
 *
 * ## The surface (draft box only — item 10 explicitly drops the recent-notes table/preview)
 *
 * Two fields: the multi-line draft (the home) and an optional title (item 3b — empty = the backend
 * auto/LLM-titles the note). `Tab` toggles the focused field. The recent-notes table and preview pane
 * the old Textual modal had are deliberately gone.
 *
 * ## What each captured key does (the FSM table → dispatch)
 *
 *  - **`escape`** — declared chord → {@link NoteCaptureState.pressEscape}. The double-tap window lives
 *    in the store; on a `'commit'` outcome the mode dismisses (keeping the draft — see below); on
 *    `'armed'` it stays.
 *  - **`d`** — context-sensitive (NOT a static chord): the delete chord ONLY while
 *    {@link NoteCaptureState.blurTimerActive} (ESC just armed it), else an ordinary character. Rides
 *    `onUncaptured`.
 *  - **`u`** — undoes the last delete only if there is a snapshot ({@link NoteCaptureState.pressUndo}),
 *    else an ordinary `u`. Rides `onUncaptured`.
 *  - **`return`** (Enter) — submit the draft if non-empty. Shift+Enter inserts a newline.
 *  - **any other printable char** — appended to the focused field.
 *
 * ## Draft persists across cancel/reopen (item 10)
 *
 * Dismiss on **cancel** (ESC double-tap) does NOT reset the FSM — the draft (and title) survive so a
 * reopen finds the in-progress capture intact. The store is reset ONLY on a **confirmed submit**, so a
 * captured note never leaks into the next one. This is the one behavior change from the FSM-only slice.
 */

import type { Key } from 'ink';
import { Box, Text } from 'ink';
import type { ReactNode } from 'react';
import { useStore } from 'zustand';
import { MultiLineText, TextInput } from '../../components/TextInput.js';
import type { Mode, ModeHint, ModeStoreApi } from '../../input/modeStore.js';
import { useTheme } from '../../theme/themeStore.js';
import type { NoteCaptureStoreApi } from './noteCaptureStore.js';

// Bring the dispatcher's `onUncaptured` augmentation of `Mode` into scope (declared in dispatcher.ts).
import '../../input/dispatcher.js';

/** The note-capture mode's declared-chord intent union. `d`/`u`/printable are NOT here — they are
 * context-sensitive and flow through `onUncaptured` (see the module doc). */
type NoteCaptureIntent = 'escape' | 'submit' | 'newline' | 'switchField';

/** Stable mode id so a re-enter is idempotent (the modeStore pattern). */
export const NOTE_CAPTURE_MODE_ID = 'note-capture';

/** Which field the surface has focused. The draft is the home; `Tab` toggles to the title. */
type CaptureField = 'draft' | 'title';

/** What the caller supplies when opening the capture screen. */
export interface NoteCaptureModeOptions {
  /** Run when the draft is submitted (Enter on non-empty text). The action layer does the bus call.
   * `title` is the optional user title (empty/undefined → backend auto/LLM-titles). */
  readonly onSubmit: (draft: string, title: string | undefined) => void;
  /** Run when the capture is cancelled (the ESC double-tap commit). The draft is kept for next open. */
  readonly onCancel: () => void;
}

/** The bottom-bar hints for the capture surface (wave 1 mode-aware BottomBar). */
export function noteCaptureHints(): readonly ModeHint[] {
  return [
    { key: 'enter', description: 'save' },
    { key: 'shift+enter', description: 'newline' },
    { key: 'tab', description: 'title' },
    { key: 'esc·esc', description: 'cancel' },
  ];
}

/**
 * Build the note-capture {@link Mode}, wiring the dispatcher to the {@link NoteCaptureStoreApi} FSM.
 * `modes` is for self-dismiss; `store` is the FSM whose verbs the handlers call.
 */
export function noteCaptureMode(
  modes: ModeStoreApi,
  store: NoteCaptureStoreApi,
  options: NoteCaptureModeOptions,
): Mode<NoteCaptureIntent> {
  const id = NOTE_CAPTURE_MODE_ID;

  // Which field is focused — closure state (not the FSM store, whose `focus` is the blur draft→list
  // machine). Persists with the mode frame for its lifetime; a reopen starts on the draft.
  let field: CaptureField = 'draft';

  /** Re-render by poking the mode store (re-enter same id → new stack ref). */
  function refresh(): void {
    const current = modes.getState().stack.find((f) => f.mode.id === id);
    if (current !== undefined) {
      modes.getState().enter(current.mode);
    }
  }

  /** Cancel-dismiss: exit WITHOUT resetting the FSM, so the draft persists across reopen (item 10). */
  function cancel(): void {
    modes.getState().exit(id);
    options.onCancel();
  }

  return {
    id,
    presentation: 'modal',
    get hints(): readonly ModeHint[] {
      return noteCaptureHints();
    },
    // No passThrough: the capture modal captures everything (Textual's ModalScreen behavior).
    keymap: [
      // ESC: every press fires here; the store decides arm-vs-commit (the double-tap FSM).
      {
        chord: { key: { escape: true } },
        intent: 'escape',
        description: 'esc·esc close / esc d clear',
      },
      // Shift+Enter inserts a newline in the draft; plain Enter submits.
      { chord: { key: { shift: true, return: true } }, intent: 'newline', description: 'newline' },
      { chord: { key: { return: true } }, intent: 'submit', description: 'save in background' },
      // Tab toggles between the draft and the title field.
      { chord: { key: { tab: true } }, intent: 'switchField', description: 'title' },
    ],
    onIntent(intent) {
      switch (intent) {
        case 'escape': {
          const outcome = store.getState().pressEscape();
          if (outcome === 'commit') {
            // ESC double-tap → cancel-without-submit. Draft is KEPT (no reset) — item 10.
            cancel();
          }
          // 'armed' → stay open; the blur timer is now ticking (handled inside the store).
          return;
        }
        case 'newline': {
          // Shift+Enter: a literal newline in the draft (a no-op while the title is focused).
          if (field === 'draft') {
            store.getState().setDraft(`${store.getState().draftText}\n`);
          }
          return;
        }
        case 'switchField': {
          field = field === 'draft' ? 'title' : 'draft';
          refresh();
          return;
        }
        case 'submit': {
          // Snapshot the fields BEFORE reset so onSubmit sees the text. Reset ONLY on a confirmed
          // submit (item 10) — a captured note never leaks into the next capture.
          const draft = store.getState().draftText;
          const title = store.getState().titleText;
          if (draft.trim() !== '') {
            store.getState().reset();
            field = 'draft';
            modes.getState().exit(id);
            options.onSubmit(draft, title.trim() === '' ? undefined : title);
          }
          return;
        }
        default:
          return intent satisfies never;
      }
    },
    // onUncaptured: the context-sensitive keys + ordinary text entry. The dispatcher calls this when
    // the declared keymap has no match (the C12 hook).
    onUncaptured(input: string, key: Key): boolean {
      // Ignore modified/special non-character events — not ours; let the dispatcher swallow them.
      if (input.length === 0 || key.ctrl || key.meta || key.escape || key.return || key.tab) {
        return false;
      }
      const state = store.getState();
      // Title field: plain text entry only (no ESC-chord behavior — those belong to the draft).
      if (field === 'title') {
        state.setTitle(state.titleText + input);
        return true;
      }
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
    render: () => <NoteCaptureSurface store={store} field={field} />,
  };
}

/** The capture surface — draft box + optional title field (no recent-notes table/preview, item 10).
 * Subscribes to the FSM store so live edits repaint; pure presentation otherwise. */
function NoteCaptureSurface({
  store,
  field,
}: {
  readonly store: NoteCaptureStoreApi;
  readonly field: CaptureField;
}): ReactNode {
  const theme = useTheme();
  const draft = useStore(store, (s) => s.draftText);
  const title = useStore(store, (s) => s.titleText);
  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={theme.heading}
      paddingX={2}
      paddingY={1}
      width={64}
    >
      <Text bold color={theme.heading}>
        Quick note
      </Text>

      <Box marginTop={1} flexDirection="column">
        <Text color={field === 'title' ? theme.text : theme.muted}>Title (optional):</Text>
        <TextInput
          value={title}
          placeholder="leave empty to auto-title"
          focused={field === 'title'}
          color={field === 'title' ? theme.text : theme.muted}
        />
      </Box>

      <Box marginTop={1} flexDirection="column">
        <Text color={field === 'draft' ? theme.text : theme.muted}>Note:</Text>
        <MultiLineText
          value={draft}
          placeholder="capture a thought…"
          focused={field === 'draft'}
          color={field === 'draft' ? theme.text : theme.muted}
        />
      </Box>
    </Box>
  );
}
