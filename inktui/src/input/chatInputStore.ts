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
 * Framework-agnostic vanilla Zustand (rule 4): no React, no Ink.
 */

import { createStore, type StoreApi } from 'zustand/vanilla';

/** The chat-input buffer state: the in-progress message text plus its edit verbs. */
export interface ChatInputState {
  /** The current message buffer (what the user has typed but not yet sent). */
  readonly text: string;
  /** Append a printable character (called per keystroke by the dispatcher's chat handler). */
  append(char: string): void;
  /** Delete the last character (Backspace). No-op on an empty buffer. */
  backspace(): void;
  /** Clear the buffer (called after a send, or to reset). */
  clear(): void;
}

/** The chat-input store handle. Re-exported so callers don't import `zustand/vanilla` directly. */
export type ChatInputStoreApi = StoreApi<ChatInputState>;

/** Create the chat-input buffer store. Starts empty. */
export function createChatInputStore(): ChatInputStoreApi {
  return createStore<ChatInputState>()((set) => ({
    text: '',
    append(char) {
      set((state) => ({ text: state.text + char }));
    },
    backspace() {
      set((state) => ({ text: state.text.length > 0 ? state.text.slice(0, -1) : '' }));
    },
    clear() {
      set({ text: '' });
    },
  }));
}
