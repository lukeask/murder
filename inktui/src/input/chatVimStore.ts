/**
 * `chatVimStore` — the **vim editing mode** state for the chat input (chat-input overhaul, user ask
 * #3): the current submode (`normal`/`insert`), the pending operator for two-key commands, and the
 * **murder-wide yank register**. One store instance is shared across every chat target, which is the
 * whole point of the register: yank in one crow's draft, paste into another's.
 *
 * ## Why these three live together (and what does NOT)
 *
 * The reducer ({@link ./chatVimReducer.js}) is a *pure* function of `(BufferState, key, pending,
 * register)` → {@link ./chatVimReducer.js VimEffect}. It owns no state; it cannot, because a pure
 * reducer must be replayable. So the three pieces of vim state the reducer reads/writes around the
 * edges live here, in the framework-agnostic store the dispatcher mutates:
 *
 *  - **`submode`** — `normal` vs `insert`. The dispatcher's chat handler (WS-E) branches on this: in
 *    `insert` it behaves like the non-vim text field (printable→insert, Esc→normal); in `normal` it
 *    routes the keystroke through `reduceVimNormal` and applies the resulting effect.
 *  - **`pending`** — the first key of a two-key command awaiting its motion/second key: `d` (delete),
 *    `c` (change), `y` (yank), `g` (the `gg` prefix). `null` when no operator is pending. The reducer
 *    is told the current `pending` and returns a `{ kind:'pending' }` effect to set/clear it; the
 *    handler writes it back here. Holding it in the store (not the reducer) keeps the reducer pure and
 *    lets the UI show a pending indicator if it wants.
 *  - **`register`** — the **murder-wide** yank register. Yank/delete/change write it (via the reducer's
 *    `setRegister`/`buffer`-with-register effects, applied by the handler); paste (`p`/`P`) reads it.
 *    Because there is exactly one store instance for the whole app, the register naturally spans chat
 *    targets — the cross-crow yank/paste the spec asks for falls out for free.
 *
 * What is deliberately NOT here: the buffer itself (that is {@link ./chatInputStore.js}, a
 * `BufferState`), and whether vim mode is *enabled at all* (that is the persisted setting
 * `settings.vimMode`, owned by WS-C/WS-D). This store is meaningful only while vim mode is on; when it
 * is off, the handler never consults it.
 *
 * Framework-agnostic vanilla Zustand (rule 4): no React, no Ink — same idiom as
 * {@link ./chatInputStore.js}/{@link ./focusStore.js}.
 */

import { createStore, type StoreApi } from 'zustand/vanilla';

/** The two vim editing submodes. `normal` routes keys through the reducer; `insert` behaves like the
 * ordinary text field (printable inserts, Esc returns to `normal`). Counts and visual mode are out of
 * scope, so there is no `visual` submode. */
export type VimSubmode = 'normal' | 'insert';

/** The vim mode state: submode + pending operator + the shared register, with the setters the
 * dispatcher's chat handler calls to write back what the pure reducer computed. */
export interface ChatVimState {
  /** Current submode. The chat handler branches on this every keystroke. Starts `normal`. */
  readonly submode: VimSubmode;
  /** Pending operator for a two-key command (`'d'`, `'c'`, `'y'`, `'g'`) awaiting its second key, or
   * `null` when none is pending. Set/cleared by the handler from the reducer's `pending` effect.
   * Entering insert mode or completing a command clears it back to `null`. */
  readonly pending: string | null;
  /** The murder-wide yank register — shared across all chat targets because there is one store
   * instance. Written by yank/delete/change, read by paste. Starts `''`. */
  readonly register: string;
  /** Set the submode (`normal`↔`insert`). */
  setSubmode(m: VimSubmode): void;
  /** Set (or clear, with `null`) the pending operator. */
  setPending(p: string | null): void;
  /** Replace the register text (yank/delete/change result). */
  setRegister(text: string): void;
}

/** The vim store handle. Re-exported so callers don't import `zustand/vanilla` directly. */
export type ChatVimStoreApi = StoreApi<ChatVimState>;

/** Create the chat vim store. Starts in `normal` submode, no pending operator, empty register. */
export function createChatVimStore(): ChatVimStoreApi {
  return createStore<ChatVimState>()((set) => ({
    submode: 'normal',
    pending: null,
    register: '',
    setSubmode(m) {
      set({ submode: m });
    },
    setPending(p) {
      set({ pending: p });
    },
    setRegister(text) {
      set({ register: text });
    },
  }));
}
