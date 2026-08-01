/**
 * History slice — domain state for the history feed (panel 5, ctrl+5).
 *
 * The history feed is a read model over the durable user-message spine
 * (`conversation_blocks kind='user'`); a row's `status` is the zero-LLM v0
 * taxonomy (`open` / `stale` / `dismissed`) derived server-side. Like the other
 * list slices it holds raw, wire-faithful domain data only — presentation
 * (loose-threads vs all ordering, relative-age formatting) lives in the selector
 * (rule 2).
 *
 * The `{ rows, status, error }` mechanics come from the shared
 * {@link createListSlice} factory; this file is a thin shell over it. The one
 * extra behaviour beyond a pure list slice — optimistic `dismiss` — lives in the
 * actions file (`./historyActions.ts`), not here (the slice holds state only).
 */

import { createListSlice, initialListState, type ListState } from '../listSlice.js';

/**
 * One history item as the slice cares about it — a presentation-free projection
 * of the service's `HistoryItemSummary` DTO. `ts` is the ISO-8601 string from
 * the wire; the selector formats relative age.
 */
export interface HistoryRow {
  readonly itemId: string;
  readonly text: string;
  readonly target: string;
  /** The conversation id (UUID) — the resume key, distinct from `target` (agent id). */
  readonly conversationId: string;
  /** ISO-8601 string as the wire delivers it. The selector formats relative age. */
  readonly ts: string;
  /** Zero-LLM v0 status: `open` | `stale` | `dismissed`. */
  readonly status: string;
  readonly harness: string | null;
  readonly conversationStatus: string;
  /** Whether this item's conversation can be resumed (drives the future /resume keybind). */
  readonly resumable: boolean;
}

/** The history slice's state — the shared {@link ListState} shape specialized to {@link HistoryRow}. */
export type HistoryState = ListState<HistoryRow>;

/** The initial, pre-fetch slice value. */
export const initialHistoryState: HistoryState = initialListState<HistoryRow>();

/** Slice factory — seeds the `history` key, built from the shared {@link createListSlice}. */
export const createHistorySlice = createListSlice('history', initialHistoryState);
