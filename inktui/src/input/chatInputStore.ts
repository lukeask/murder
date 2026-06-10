/**
 * `chatInputStore` — the chat message buffer for the **persistent chat-input mode** (C11, part F).
 *
 * Chat is the app's permanent focus home, not a transient surface, so its input buffer is NOT a
 * {@link ./modeStore.js modeStore} frame (that primitive is capture + focus-restore, which chat does
 * not want — there is nothing to restore to and no dismiss). Instead the buffer is a tiny piece of
 * UI state held here, mutated by the dispatcher's layer-2 chat handler (see {@link ./dispatcher.js}'s
 * `ChatInputHandler`) and read by the {@link ../components/ChatInput.js ChatInput} component to render
 * the live text + cursor.
 *
 * Why a store and not component `useState`: the dispatcher (the ONE root input owner — rule 5) must
 * mutate the buffer on every keystroke, but the dispatcher is not a component and cannot hold React
 * state. A vanilla Zustand store is the framework-agnostic seam (rule 4) both the dispatcher handler
 * and the component read/write — exactly the panel/focus/mode pattern, kept consistent. The send
 * *action* (the bus call) is NOT here — that is the conversations action (rule 3); this store holds
 * only the buffer text, and `clear()` is called by the send path after the message is dispatched.
 *
 * ## Marked image spans (F9 image-paste UX)
 *
 * A pasted image is represented in the buffer as an **atomic marked span**: the stable image `id`
 * wrapped in invisible Unicode Private-Use-Area delimiters — `U+E000 <id> U+E001`. The buffer holds
 * the *id*, never the visible `[Image N]` number; `N` is **derived at render** by counting marked
 * spans before each one (see {@link spanLabels}), so deleting one renumbers the rest for free and the
 * id-keyed {@link ../store/imageDraft/imageDraftStore.js imageDraftStore} map never drifts.
 *
 * The text caret is end-only (this buffer has no cursor offset — append-at-end, backspace-at-end), so
 * "the caret cannot land inside a span" reduces to two concrete rules: {@link ChatInputState.append}
 * never splits a span (it only ever appends after the last one), and {@link ChatInputState.backspace}
 * at a span's trailing edge removes the **whole** span and returns its id, so the handler can drop the
 * matching imageDraftStore entry (and cancel/ignore its in-flight upload). This keeps the store
 * dependency-free — it knows the *span format* but never imports imageDraftStore (rule 3 shape: the
 * cross-store wiring lives in the handler, like `send`).
 *
 * Framework-agnostic vanilla Zustand (rule 4): no React, no Ink.
 */

import { createStore, type StoreApi } from 'zustand/vanilla';

/** The opening PUA delimiter of a marked image span. */
export const SPAN_OPEN = '';
/** The closing PUA delimiter of a marked image span. */
export const SPAN_CLOSE = '';

/** Matches one whole marked span, capturing the inner id. Global so {@link spanLabels} / expansion can
 * walk every span. The id is any run of non-delimiter chars (ids are uuid+timestamp stems — no PUA). */
const SPAN_RE = new RegExp(`${SPAN_OPEN}([^${SPAN_OPEN}${SPAN_CLOSE}]*)${SPAN_CLOSE}`, 'g');

/** Wrap an image id into its marked-span text form: `U+E000 <id> U+E001`. */
export function makeSpan(id: string): string {
  return `${SPAN_OPEN}${id}${SPAN_CLOSE}`;
}

/** The chat-input buffer state: the in-progress message text plus its edit verbs. */
export interface ChatInputState {
  /** The current message buffer — printable text interleaved with marked image spans (the buffer
   * holds image *ids*, not the visible `[Image N]` labels, which are derived at render). */
  readonly text: string;
  /** Append a printable character (called per keystroke by the dispatcher's chat handler). */
  append(char: string): void;
  /** Append a marked image span carrying `id` (called on paste, after the imageDraftStore mints the
   * id). The span is atomic — a later backspace removes it whole. */
  appendImageSpan(id: string): void;
  /**
   * Delete at the buffer's trailing edge. If the buffer ends in a marked image span, the **whole**
   * span is removed and its id is returned (so the handler can drop the imageDraftStore entry +
   * cancel its upload). Otherwise one character is removed and `null` is returned. No-op on an empty
   * buffer (returns `null`).
   */
  backspace(): string | null;
  /** Clear the buffer (called after a send, or to reset). */
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

/** Create the chat-input buffer store. Starts empty. */
export function createChatInputStore(): ChatInputStoreApi {
  return createStore<ChatInputState>()((set, get) => ({
    text: '',
    append(char) {
      set((state) => ({ text: state.text + char }));
    },
    appendImageSpan(id) {
      set((state) => ({ text: state.text + makeSpan(id) }));
    },
    backspace() {
      const { text } = get();
      if (text.length === 0) {
        return null;
      }
      // Trailing edge is a marked span → remove the whole span, return its id.
      if (text.endsWith(SPAN_CLOSE)) {
        const open = text.lastIndexOf(SPAN_OPEN);
        if (open !== -1) {
          const id = text.slice(open + 1, text.length - 1);
          set({ text: text.slice(0, open) });
          return id;
        }
      }
      // Otherwise delete one character at the end.
      set({ text: text.slice(0, -1) });
      return null;
    },
    clear() {
      set({ text: '' });
    },
  }));
}
