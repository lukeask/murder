/**
 * History view-models — the selector (rule 2: presentation lives here, never in the store).
 *
 * The history feed has two modes (the panel's `a` toggle). Both order newest-first (by last
 * user-message send time); the only difference is the filter:
 *  - **loose** (default): the "loose threads" radar — OPEN + STALE items only. DISMISSED rows are
 *    hidden.
 *  - **all**: the full firehose, every row including dismissed.
 *
 * The wire delivers items newest-first; this selector re-sorts (defensively) and formats each row's
 * relative age + status tag. Two layers (same as the other selectors): a pure transform
 * (`selectHistoryView`) and a `useMemo` hook (`useHistoryView`).
 */

import { useMemo } from 'react';
import type { HistoryRow, HistoryState } from '../store/history/historySlice.js';

/** The panel's filter mode — the `a` key toggles between them. */
export type HistoryMode = 'loose' | 'all';

/** One history row as the component paints it: display-ready strings. */
export interface HistoryRowView {
  readonly itemId: string;
  /** The intention text (the panel truncates to fit the column). */
  readonly text: string;
  /** Who/what it was aimed at (agent id). */
  readonly target: string;
  /** The conversation id used to resume — distinct from `target`. */
  readonly conversationId: string;
  /** Relative age, e.g. `"3h"`, `"2d"`, `"just now"`. */
  readonly age: string;
  /** Short uppercase status tag: `OPEN` / `STALE` / `DISMISSED`. */
  readonly statusTag: string;
  /** Raw status (for color selection in the component). */
  readonly status: string;
  /** Whether the item's conversation is resumable (drives the future /resume keybind affordance). */
  readonly resumable: boolean;
}

/** The whole history list, render-ready. */
export interface HistoryView {
  readonly rows: readonly HistoryRowView[];
  readonly status: HistoryState['status'];
  readonly error: string | null;
  readonly isEmpty: boolean;
  /** Count of loose threads (OPEN + STALE) — the header digest, independent of the active mode. */
  readonly looseCount: number;
}

/** Format an ISO-8601 timestamp as a compact relative age against `now`. Pure. */
export function formatRelativeAge(iso: string, now: number): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) {
    return '?';
  }
  const seconds = Math.max(0, Math.floor((now - then) / 1000));
  if (seconds < 60) {
    return 'just now';
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours}h`;
  }
  const days = Math.floor(hours / 24);
  return `${days}d`;
}

/** Whether a row is a "loose thread" — an un-terminal intention (OPEN or STALE). */
function isLoose(row: HistoryRow): boolean {
  return row.status === 'open' || row.status === 'stale';
}

function toHistoryRowView(row: HistoryRow, now: number): HistoryRowView {
  return {
    itemId: row.itemId,
    text: row.text,
    target: row.target,
    conversationId: row.conversationId,
    age: formatRelativeAge(row.ts, now),
    statusTag: row.status.toUpperCase(),
    status: row.status,
    resumable: row.resumable,
  };
}

/** Compare by `ts` ISO string descending (lexicographic == chronological): newest first. */
function byTsDesc(a: HistoryRow, b: HistoryRow): number {
  return b.ts.localeCompare(a.ts);
}

/**
 * The pure view-model transform. Both modes order newest-first; `loose` keeps only OPEN/STALE rows,
 * `all` keeps every row (including dismissed). `now` is injected (testable; the hook passes
 * `Date.now()`). Never mutates the slice's readonly array.
 */
export function selectHistoryView(
  state: HistoryState,
  mode: HistoryMode,
  now: number,
): HistoryView {
  const looseCount = state.rows.filter(isLoose).length;
  const filtered = mode === 'loose' ? state.rows.filter(isLoose) : [...state.rows];
  const ordered = filtered.sort(byTsDesc);
  const rows = ordered.map((row) => toHistoryRowView(row, now));
  return {
    rows,
    status: state.status,
    error: state.error,
    isEmpty: rows.length === 0,
    looseCount,
  };
}

/**
 * Component-facing hook: memoizes {@link selectHistoryView} on the slice identity + mode. `now` is
 * captured per-render via `Date.now()` and folded into the memo key bucket (minute granularity) so
 * relative ages refresh without re-running on every render.
 */
export function useHistoryView(state: HistoryState, mode: HistoryMode): HistoryView {
  // Bucket `now` to the minute so the memo doesn't recompute on every render but ages still tick.
  const nowBucket = Math.floor(Date.now() / 60000);
  return useMemo(
    () => selectHistoryView(state, mode, nowBucket * 60000),
    [state, mode, nowBucket],
  );
}
