/**
 * `chatHistoryStore` — the **murder-wide ring of sent user messages** (chat-input overhaul, user ask
 * #4). It is the corpus that the chat field scrolls back through when the cursor is on the top visual
 * row and `up` is pressed: a recall of *previously-sent* messages, drawn across **all** crows, not just
 * the active recipient target. Yank-in-one-chat / paste-into-another is the register's job (see
 * {@link ./chatVimStore.js}); this store is the *send-history* analogue, equally murder-wide.
 *
 * ## Why this is just the corpus (and where navigation lives)
 *
 * History *navigation* state — the cursor into the ring, the stashed live draft — is NOT here. It lives
 * in {@link ./chatInputStore.js chatInputStore} (WS-E), because walking history is inseparable from the
 * live draft: pressing up at the top row must *stash* the in-progress buffer, load an entry, and later
 * *restore* the draft when you walk back down past the newest entry. That coupling belongs with the
 * buffer it mutates. This store holds only the immutable-ish **entries** plus the two verbs that grow
 * it ({@link ChatHistoryState.record} at the send boundary) or reseed it wholesale
 * ({@link ChatHistoryState.seed} from a fresh conversations snapshot). The navigation reads `entries`
 * as a read-only array and indexes into it.
 *
 * ## Seeding vs recording — two write paths, one corpus
 *
 * The transcript feed (`state.conversations_snapshot`) is authoritative and re-pulled on every
 * (re)connect, so on boot/refresh the app collects every `type==='user'` `raw.text` across all
 * transcripts, sorts oldest→newest by numeric block id (the `selectUserHistory` selector, owned by
 * WS-E), and calls {@link ChatHistoryState.seed} to replace the whole ring. Between snapshots, each
 * successful send calls {@link ChatHistoryState.record} to append the just-sent message immediately
 * (so the user can recall what they typed a moment ago without waiting for the round-trip). A reseed
 * may then re-derive the same entry from the snapshot — the dedupe rule (no consecutive duplicates)
 * keeps that from doubling, and seed replaces wholesale so it self-heals regardless.
 *
 * Framework-agnostic vanilla Zustand (rule 4): no React, no Ink — the exact idiom as
 * {@link ./chatInputStore.js}/{@link ./focusStore.js}.
 */

import { createStore, type StoreApi } from 'zustand/vanilla';

/** The murder-wide sent-message ring and its two write verbs. Read `entries` (oldest→newest) for
 * recall; mutate only via {@link ChatHistoryState.record} (send boundary) or
 * {@link ChatHistoryState.seed} (snapshot reseed). */
export interface ChatHistoryState {
  /** Sent messages, oldest→newest, deduped against the immediately-previous entry. Read-only to
   * callers; the navigation state in {@link ./chatInputStore.js} indexes into this array. Replaced
   * wholesale on every write so a `useStore` subscriber re-renders on change. */
  readonly entries: readonly string[];
  /**
   * Record a just-sent message (called at the send boundary, after dispatch). No-op for the empty
   * string or a message identical to the **last** entry — consecutive duplicates are collapsed so
   * repeatedly sending the same line does not bloat the recall ring with redundant stops (matching the
   * familiar shell-history feel). Non-consecutive duplicates are kept (you may legitimately revisit an
   * old message later).
   */
  record(text: string): void;
  /**
   * Replace the **whole** ring — the snapshot reseed path. The caller (WS-E's `selectUserHistory`)
   * passes the entries already sorted oldest→newest; this verb stores them verbatim. Wholesale
   * replacement (not a merge) is what makes a reseed self-healing: whatever {@link record} appended
   * optimistically between snapshots is reconciled to exactly what the authoritative snapshot says.
   */
  seed(entries: readonly string[]): void;
}

/** The chat-history store handle. Re-exported so callers don't import `zustand/vanilla` directly. */
export type ChatHistoryStoreApi = StoreApi<ChatHistoryState>;

/** Create the murder-wide sent-message history store. Starts empty; seeded from the first snapshot and
 * grown per send. */
export function createChatHistoryStore(): ChatHistoryStoreApi {
  return createStore<ChatHistoryState>()((set, get) => ({
    entries: [],
    record(text) {
      // Drop empties and consecutive duplicates — the recall ring is a list of *distinct* recent
      // sends, not a raw send log (the durable log lives server-side).
      if (text === '') {
        return;
      }
      const { entries } = get();
      if (entries[entries.length - 1] === text) {
        return;
      }
      set({ entries: [...entries, text] });
    },
    seed(entries) {
      // Wholesale replace — copy so a later mutation of the caller's array cannot alias our state.
      set({ entries: [...entries] });
    },
  }));
}
