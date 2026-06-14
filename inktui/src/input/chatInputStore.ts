/**
 * `chatInputStore` — the chat message buffer for the **persistent chat-input mode** (C11, part F),
 * rebuilt on the {@link ./chatBuffer.js chatBuffer} cursor model for the chat-input overhaul (WS-E).
 *
 * Chat is the app's permanent focus home, not a transient surface, so its input buffer is NOT a
 * {@link ./modeStore.js modeStore} frame (that primitive is capture + focus-restore, which chat does
 * not want — there is nothing to restore to and no dismiss). Instead the buffer is a tiny piece of
 * UI state held here, mutated by the dispatcher's layer-2 chat handler (see {@link ./dispatcher.js}'s
 * `ChatInputHandler`) and read by the {@link ../components/ChatInput.js ChatInput} component to render
 * the live text + cursor.
 *
 * ## The buffer is now a {@link BufferState} (text + cursor)
 *
 * The store holds one live `BufferState` (`{ text, cursor }`) and routes every edit/motion through the
 * pure chatBuffer ops. The component renders via {@link ./chatBuffer.js layout}; the dispatcher's
 * handler (WS-E, App.tsx) calls the edit/move actions and the history-nav actions. `text`/`cursor`
 * getters and `clear()` are kept (ChatInput + the send path + the freeform-choice takeover read them);
 * `append`/`appendImageSpan`/`backspace` shims are kept (the freeform-choice takeover still uses them).
 *
 * ## History navigation (user ask #4)
 *
 * Walking the murder-wide sent-message history is inseparable from the live draft (pressing `up` at the
 * top visual row stashes the in-progress buffer, loads an older entry, and walking back `down` past the
 * newest entry restores the stashed draft). That coupling lives HERE — {@link ChatInputState.historyIndex}
 * (`null` = editing the live draft) and {@link ChatInputState.stashedDraft} — not in
 * {@link ./chatHistoryStore.js} (which is just the corpus). {@link ChatInputState.historyPrev} /
 * {@link ChatInputState.historyNext} take the history `entries` and load/stash accordingly.
 *
 * ## Marked image spans (F9 image-paste UX)
 *
 * A pasted image is represented in the buffer as an **atomic marked span**: the stable image `id`
 * wrapped in invisible Unicode Private-Use-Area delimiters — `U+E000 <id> U+E001`. The buffer holds
 * the *id*, never the visible `[Image N]` number; `N` is **derived at render** by counting marked
 * spans before each one (see {@link spanLabels}). The cursor never lands inside a span; chatBuffer's
 * edit/motion ops snap over a whole span as one unit, and {@link ChatInputState.backspace} at a span's
 * trailing edge removes the whole span and returns its id (so the handler can drop the imageDraftStore
 * entry).
 *
 * Framework-agnostic vanilla Zustand (rule 4): no React, no Ink.
 */

import { createStore, type StoreApi } from 'zustand/vanilla';
import {
  type BufferState,
  backspace as bufBackspace,
  deleteForward as bufDeleteForward,
  insert as bufInsert,
  insertImageSpan as bufInsertImageSpan,
  moveBufferEnd as bufMoveBufferEnd,
  moveBufferStart as bufMoveBufferStart,
  moveLeft as bufMoveLeft,
  moveLineEnd as bufMoveLineEnd,
  moveLineStart as bufMoveLineStart,
  moveRight as bufMoveRight,
  EMPTY_BUFFER,
} from './chatBuffer.js';

/** The opening PUA delimiter of a marked image span. Written as an explicit escape (NOT a literal
 * invisible glyph) so an editor/copy-paste can never silently strip it — an empty delimiter makes the
 * span scanner's `indexOf('')` loop forever. */
export const SPAN_OPEN = '\u{E000}';
/** The closing PUA delimiter of a marked image span. Explicit escape for the same reason as
 * {@link SPAN_OPEN}. */
export const SPAN_CLOSE = '\u{E001}';

/** Matches one whole marked span, capturing the inner id. Global so {@link spanLabels} / expansion can
 * walk every span. The id is any run of non-delimiter chars (ids are uuid+timestamp stems — no PUA). */
const SPAN_RE = new RegExp(`${SPAN_OPEN}([^${SPAN_OPEN}${SPAN_CLOSE}]*)${SPAN_CLOSE}`, 'g');

/** Wrap an image id into its marked-span text form: `U+E000 <id> U+E001`. */
export function makeSpan(id: string): string {
  return `${SPAN_OPEN}${id}${SPAN_CLOSE}`;
}

/** The chat-input buffer state: the in-progress message + cursor, its edit/motion verbs, and the
 * murder-wide-history navigation state/verbs. */
export interface ChatInputState {
  /** The live buffer (text + cursor). Mutated only via the verbs below; ref-swapped on each edit. */
  readonly buffer: BufferState;
  /**
   * History-navigation cursor: `null` while editing the live draft; otherwise an index into the
   * history `entries` array the handler passes to {@link ChatInputState.historyPrev}/
   * {@link ChatInputState.historyNext}. Any direct edit (insert/backspace/…) resets it to `null` so a
   * recalled entry the user starts editing becomes the new live draft.
   */
  readonly historyIndex: number | null;
  /** The live draft stashed when the user first walked `up` into history, restored when they walk
   * back `down` past the newest entry. `null` when not in history. */
  readonly stashedDraft: BufferState | null;
  /** The current message buffer text — printable text interleaved with marked image spans (the buffer
   * holds image *ids*, not the visible `[Image N]` labels, which are derived at render). */
  readonly text: string;
  /** The current cursor character offset into {@link ChatInputState.text}. */
  readonly cursor: number;

  /** Insert `str` at the cursor (printable typing / shift+enter newline). Resets history-nav. */
  insert(str: string): void;
  /** Insert a marked image span carrying `id` at the cursor (paste). Resets history-nav. */
  insertImageSpan(id: string): void;
  /**
   * Delete the char/span immediately before the cursor (Backspace). A span at the trailing edge is
   * removed whole and its id returned (so the handler drops the imageDraftStore entry); otherwise one
   * char. Returns `null` on a no-op or a plain-char delete. Resets history-nav.
   */
  backspace(): string | null;
  /** Delete the char/span at the cursor (Delete key / vim x). Same return shape as
   * {@link ChatInputState.backspace}. Resets history-nav. */
  deleteForward(): string | null;

  /** Move the cursor one char left (snapping over a whole span). */
  moveLeft(): void;
  /** Move the cursor one char right (snapping over a whole span). */
  moveRight(): void;
  /** Move the cursor to the start of the current logical line (Home). */
  moveLineStart(): void;
  /** Move the cursor to the end of the current logical line (End). */
  moveLineEnd(): void;
  /** Move the cursor to the very start of the buffer. */
  moveBufferStart(): void;
  /** Move the cursor to the very end of the buffer. */
  moveBufferEnd(): void;

  /** Replace the whole buffer (used by the vim reducer's effects + visual j/k motion). Does NOT reset
   * history-nav (a vim motion within a recalled entry should not snap back to the live draft). */
  setBuffer(buffer: BufferState): void;

  /**
   * Recall the previous (older) history entry (`up` on the top visual row). On the first step it
   * stashes the live draft; subsequent steps walk older. Loads the entry as the buffer with the cursor
   * at its end. No-op when `entries` is empty or already at the oldest. `entries` is oldest→newest.
   */
  historyPrev(entries: readonly string[]): void;
  /**
   * Walk forward (newer) through history (`down` on the bottom visual row). Loads the next-newer
   * entry; stepping past the newest entry restores the stashed live draft and clears history-nav.
   * No-op when not currently navigating history.
   */
  historyNext(entries: readonly string[]): void;

  /** Append a printable character at the cursor (compat shim for the freeform-choice takeover). */
  append(char: string): void;
  /** Append a marked image span at the cursor (compat shim). */
  appendImageSpan(id: string): void;
  /** Clear the buffer and any history-nav state (called after a send, or to reset). */
  clear(): void;
}

/** The chat-input store handle. Re-exported so callers don't import `zustand/vanilla` directly. */
export type ChatInputStoreApi = StoreApi<ChatInputState>;

/**
 * Derive the visible `[Image N]` label for every marked span in `text`, in order. Returns a list of
 * `{ id, label }` — the Nth span (1-based) gets `[Image N]`. Pure: the render layer interleaves these
 * with the surrounding plain text, so deletion renumbers for free (counting is positional, not stored).
 */
export function spanLabels(text: string): readonly { id: string; label: string }[] {
  const out: { id: string; label: string }[] = [];
  for (const match of text.matchAll(SPAN_RE)) {
    const id = match[1] ?? '';
    out.push({ id, label: `[Image ${out.length + 1}]` });
  }
  return out;
}

/**
 * Expand the buffer for submission: replace each marked span with its outgoing form, derived from the
 * `id → path` map (the imageDraftStore's `pathsById()`). Pure and policy-driven (the submit-while-
 * uploading decision lives in the handler, which decides whether to call this at all and what map to
 * pass):
 *
 *  - A span whose id is in `pathsById` (status `done`) expands to `![image]({path})`.
 *  - A span whose id is **absent** from the map (failed, or never finished) is **stripped** — it
 *    yields empty text. A failed upload thus never traps the buffer; the in-text marker the user saw
 *    is dropped from the outgoing markdown.
 *
 * The handler is responsible for *blocking* submit while any span is still `uploading` (those have no
 * path yet); by the time it calls this, every remaining span is either `done` (→ path) or `failed`
 * (→ stripped). Plain text passes through untouched.
 */
export function expandSpans(text: string, pathsById: ReadonlyMap<string, string>): string {
  return text.replace(SPAN_RE, (_whole, id: string) => {
    const path = pathsById.get(id);
    return path === undefined ? '' : `![image](${path})`;
  });
}

/** Collect the ids of every marked span currently in `text`, in order. Used by the handler to decide
 * the submit-while-uploading policy (block if any is still uploading) and to clear drafts after send. */
export function spanIds(text: string): readonly string[] {
  const ids: string[] = [];
  for (const match of text.matchAll(SPAN_RE)) {
    ids.push(match[1] ?? '');
  }
  return ids;
}

/** Create the chat-input buffer store. Starts empty (cursor 0, no history-nav). */
export function createChatInputStore(): ChatInputStoreApi {
  return createStore<ChatInputState>()((set, get) => {
    /** Set a new buffer + the derived `text`/`cursor` getters, resetting history-nav. The common path
     * for any direct edit: starting to type/delete on a recalled entry makes it the new live draft. */
    const setEditing = (buffer: BufferState): void => {
      set({
        buffer,
        text: buffer.text,
        cursor: buffer.cursor,
        historyIndex: null,
        stashedDraft: null,
      });
    };
    /** Apply a pure cursor-motion op to the live buffer, syncing the flat `cursor` read (the text is
     * unchanged by a motion). Does NOT reset history-nav — moving within a recalled entry is fine. */
    const move = (op: (s: BufferState) => BufferState): void => {
      const buffer = op(get().buffer);
      set({ buffer, text: buffer.text, cursor: buffer.cursor });
    };
    return {
      buffer: EMPTY_BUFFER,
      historyIndex: null,
      stashedDraft: null,
      text: '',
      cursor: 0,

      insert(str) {
        setEditing(bufInsert(get().buffer, str));
      },
      insertImageSpan(id) {
        setEditing(bufInsertImageSpan(get().buffer, id));
      },
      backspace() {
        const { state, removedId } = bufBackspace(get().buffer);
        setEditing(state);
        return removedId;
      },
      deleteForward() {
        const { state, removedId } = bufDeleteForward(get().buffer);
        setEditing(state);
        return removedId;
      },

      moveLeft() {
        move(bufMoveLeft);
      },
      moveRight() {
        move(bufMoveRight);
      },
      moveLineStart() {
        move(bufMoveLineStart);
      },
      moveLineEnd() {
        move(bufMoveLineEnd);
      },
      moveBufferStart() {
        move(bufMoveBufferStart);
      },
      moveBufferEnd() {
        move(bufMoveBufferEnd);
      },

      setBuffer(buffer) {
        set({ buffer, text: buffer.text, cursor: buffer.cursor });
      },

      historyPrev(entries) {
        if (entries.length === 0) {
          return;
        }
        const { historyIndex, buffer } = get();
        if (historyIndex === null) {
          // First step into history — stash the live draft, load the newest entry.
          const entry = entries[entries.length - 1] ?? '';
          set({
            stashedDraft: buffer,
            historyIndex: entries.length - 1,
            buffer: { text: entry, cursor: entry.length },
            text: entry,
            cursor: entry.length,
          });
          return;
        }
        if (historyIndex === 0) {
          return; // already at the oldest entry
        }
        const idx = historyIndex - 1;
        const entry = entries[idx] ?? '';
        set({
          historyIndex: idx,
          buffer: { text: entry, cursor: entry.length },
          text: entry,
          cursor: entry.length,
        });
      },
      historyNext(entries) {
        const { historyIndex, stashedDraft } = get();
        if (historyIndex === null) {
          return; // not navigating history
        }
        if (historyIndex >= entries.length - 1) {
          // Past the newest entry → restore the stashed live draft.
          const draft = stashedDraft ?? EMPTY_BUFFER;
          set({
            historyIndex: null,
            stashedDraft: null,
            buffer: draft,
            text: draft.text,
            cursor: draft.cursor,
          });
          return;
        }
        const idx = historyIndex + 1;
        const entry = entries[idx] ?? '';
        set({
          historyIndex: idx,
          buffer: { text: entry, cursor: entry.length },
          text: entry,
          cursor: entry.length,
        });
      },

      append(char) {
        setEditing(bufInsert(get().buffer, char));
      },
      appendImageSpan(id) {
        setEditing(bufInsertImageSpan(get().buffer, id));
      },
      clear() {
        set({
          buffer: EMPTY_BUFFER,
          text: '',
          cursor: 0,
          historyIndex: null,
          stashedDraft: null,
        });
      },
    };
  });
}
