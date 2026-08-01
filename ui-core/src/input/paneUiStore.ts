/**
 * `paneUiStore` — the **per-pane ephemeral UI state** that used to live in each pane controller's
 * `useState` (scroll offsets, list cursors). Hoisting it out of component state (workspaces plan,
 * step 1) means a pane's scroll/cursor *survives* the controller unmounting and remounting — which
 * is exactly what happens when a panel toggles off/on or a workspace is switched. State keyed by the
 * pane's id, not by React instance, so remounting the same pane rehydrates its position.
 *
 * ## Two maps, one store — the copy-pasteable shape
 *
 * Each kind of hoisted state is its own `Record<id, value>` field plus a single `set…` verb. Today
 * that is {@link PaneUiState.cursors} (list panes' clamped selection),
 * {@link PaneUiState.scrolls} (document/scroll panes' offset),
 * {@link PaneUiState.expandeds} (panes' expanded/maximized toggle), and
 * {@link PaneUiState.historyModes} (history pane's loose/all filter),
 * {@link PaneUiState.gotoLines} (transcript pane's pending goto-line). Rolling a new pane's `useState` into
 * the store (step 1b) is one more typed map + one more verb here, one selector hook in
 * {@link ../hooks/useInputStores.js}, and swapping the pane's `useState` for that hook — no
 * restructuring. Reads default a missing id to `0` (a pane that has never scrolled is at the top),
 * so callers never branch on "not yet seen".
 *
 * Writes replace the whole map wholesale (copy-on-write of the one changed key) so a `useStore`
 * subscriber selecting `s.cursors[id]` re-renders on change. Values are stored *unclamped*; the
 * consuming hook clamps on read against the live `rowCount`/window (matching the old in-component
 * behaviour, where the row count can shrink under a stored cursor between renders).
 *
 * Framework-agnostic vanilla Zustand (rule 4): no React, no Ink — the exact idiom as
 * {@link ./chatHistoryStore.js}/{@link ./chatInputStore.js}.
 */

import { createStore, type StoreApi } from 'zustand/vanilla';
import type { HistoryMode } from '../selectors/historySelectors.js';
import type { TransitCursor } from '../selectors/transitSelectors.js';

/** Default tree-pane cursor when no id has been written yet (lane 0, no sha). */
export const DEFAULT_TRANSIT_CURSOR: TransitCursor = { laneIndex: 0, sha: null };

/** Per-pane scroll/cursor UI state, keyed by pane id. Read `cursors[id]` / `scrolls[id]` (default
 * `0`) / `expandeds[id]` (default `false`) / `historyModes[id]` (default `'loose'`) /
 * `gotoLines[id]` (default `null`); mutate only via {@link PaneUiState.setCursor} /
 * {@link PaneUiState.setScroll} / {@link PaneUiState.setExpanded} /
 * {@link PaneUiState.setHistoryMode} / {@link PaneUiState.setGotoLine}. */
export interface PaneUiState {
  /** List panes' selection index, keyed by pane id. Stored unclamped — the consuming hook
   * ({@link ../components/panes/shared/useClampedCursor.js usePaneUiClampedCursor}) clamps on read
   * against the live row count. Missing id ⇒ `0`. Replaced wholesale on write. */
  readonly cursors: Readonly<Record<string, number>>;
  /** Document/scroll panes' scroll offset, keyed by pane id. Stored unclamped — the pane clamps on
   * read against its live window. Missing id ⇒ `0`. Replaced wholesale on write. */
  readonly scrolls: Readonly<Record<string, number>>;
  /** Panes' expanded/maximized toggle, keyed by pane id. Missing id ⇒ `false`. Replaced wholesale on
   * write. */
  readonly expandeds: Readonly<Record<string, boolean>>;
  /** History pane filter mode (`loose` ↔ `all`), keyed by pane id. Missing id ⇒ `'loose'`. Replaced
   * wholesale on write. */
  readonly historyModes: Readonly<Record<string, HistoryMode>>;
  /** Transcript pane pending goto-line (`g` prefix), keyed by pane id. Missing id ⇒ `null`. Replaced
   * wholesale on write. */
  readonly gotoLines: Readonly<Record<string, number | null>>;
  /** Tree panes' lane/sha cursor, keyed by pane id. Stored unclamped — the consuming hook clamps
   * `laneIndex` on read against the live lane count. Missing id ⇒ {@link DEFAULT_TRANSIT_CURSOR}.
   * Replaced wholesale on write. */
  readonly transitCursors: Readonly<Record<string, TransitCursor>>;
  /** Tree panes' pending `g`-jump buffer, keyed by pane id. `null` means not in `g` mode. Missing id
   * ⇒ `null`. Replaced wholesale on write. */
  readonly gBuffers: Readonly<Record<string, string | null>>;
  /** Set the selection index for a pane. Unclamped — the caller passes the value it wants stored;
   * clamping is a read-time concern. */
  setCursor(id: string, cursor: number): void;
  /** Set the scroll offset for a pane. Unclamped — the caller passes the value it wants stored;
   * clamping is a read-time concern. */
  setScroll(id: string, scroll: number): void;
  /** Set the expanded toggle for a pane. */
  setExpanded(id: string, expanded: boolean): void;
  /** Set the history filter mode for a pane. */
  setHistoryMode(id: string, mode: HistoryMode): void;
  /** Set the pending goto-line for a pane (`null` clears). */
  setGotoLine(id: string, gotoLine: number | null): void;
  /** Set the tree-pane transit cursor for a pane. Unclamped — the caller passes the value it wants
   * stored; lane-index clamping is a read-time concern. */
  setTransitCursor(id: string, cursor: TransitCursor): void;
  /** Set the tree-pane `g`-jump buffer for a pane. */
  setGBuffer(id: string, buffer: string | null): void;
}

/** The pane-UI store handle. Re-exported so callers don't import `zustand/vanilla` directly. */
export type PaneUiStoreApi = StoreApi<PaneUiState>;

/** Create the per-pane UI-state store. Starts with no remembered positions; every id defaults to
 * `0` until first written. */
export function createPaneUiStore(): PaneUiStoreApi {
  return createStore<PaneUiState>()((set, get) => ({
    cursors: {},
    scrolls: {},
    expandeds: {},
    historyModes: {},
    gotoLines: {},
    transitCursors: {},
    gBuffers: {},
    setCursor(id, cursor) {
      // Copy-on-write the one key so a subscriber selecting `s.cursors[id]` re-renders.
      set({ cursors: { ...get().cursors, [id]: cursor } });
    },
    setScroll(id, scroll) {
      set({ scrolls: { ...get().scrolls, [id]: scroll } });
    },
    setExpanded(id, expanded) {
      set({ expandeds: { ...get().expandeds, [id]: expanded } });
    },
    setHistoryMode(id, mode) {
      set({ historyModes: { ...get().historyModes, [id]: mode } });
    },
    setGotoLine(id, gotoLine) {
      set({ gotoLines: { ...get().gotoLines, [id]: gotoLine } });
    },
    setTransitCursor(id, cursor) {
      set({ transitCursors: { ...get().transitCursors, [id]: cursor } });
    },
    setGBuffer(id, buffer) {
      set({ gBuffers: { ...get().gBuffers, [id]: buffer } });
    },
  }));
}
