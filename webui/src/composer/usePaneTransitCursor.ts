/**
 * Store-backed transit cursor for the tree panel — same paneUiStore pattern as the TUI,
 * without importing inktui. Survives panel remount / workspace switch.
 */

import { useCallback, type Dispatch, type SetStateAction } from 'react';
import { useStoreWithEqualityFn } from 'zustand/traditional';
import {
  DEFAULT_TRANSIT_CURSOR,
  type PaneUiStoreApi,
} from '@murder/ui-core/input/paneUiStore.js';
import type { TransitCursor } from '@murder/ui-core/selectors/transitSelectors.js';
import { useComposerStores } from './ComposerStoresProvider.js';

function clampTransitCursor(cursor: TransitCursor, laneCount: number): TransitCursor {
  if (laneCount <= 0) return cursor;
  const laneIndex = Math.min(Math.max(cursor.laneIndex, 0), laneCount - 1);
  return laneIndex === cursor.laneIndex ? cursor : { ...cursor, laneIndex };
}

/** `[cursor, setCursor]` for pane id, clamped against live lane count. */
export function usePaneTransitCursor(
  id: string,
  laneCount: number,
): [TransitCursor, Dispatch<SetStateAction<TransitCursor>>] {
  const { paneUi } = useComposerStores();
  const cursorState = useStoreWithEqualityFn(
    paneUi,
    (s) => s.transitCursors[id] ?? DEFAULT_TRANSIT_CURSOR,
  );
  const cursor = clampTransitCursor(cursorState, laneCount);

  const setCursor: Dispatch<SetStateAction<TransitCursor>> = useCallback(
    (action) => {
      const api: PaneUiStoreApi = paneUi;
      const current = api.getState().transitCursors[id] ?? DEFAULT_TRANSIT_CURSOR;
      const next = typeof action === 'function' ? action(current) : action;
      api.getState().setTransitCursor(id, next);
    },
    [paneUi, id],
  );

  return [cursor, setCursor];
}
