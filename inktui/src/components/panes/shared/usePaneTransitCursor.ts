import type { Dispatch, SetStateAction } from 'react';
import { useCallback } from 'react';
import { useInputStores, usePaneUiStore } from '../../../hooks/useInputStores.js';
import { DEFAULT_TRANSIT_CURSOR, type PaneUiStoreApi } from '../../../input/paneUiStore.js';
import type { TransitCursor } from '../../../selectors/transitSelectors.js';

function clampTransitCursor(cursor: TransitCursor, laneCount: number): TransitCursor {
  if (laneCount <= 0) {
    return cursor;
  }
  const laneIndex = Math.min(Math.max(cursor.laneIndex, 0), laneCount - 1);
  return laneIndex === cursor.laneIndex ? cursor : { ...cursor, laneIndex };
}

/**
 * Store-backed drop-in for `const [cursor, setCursor] = useState<TransitCursor>(…)` in the tree
 * pane — the cursor lives in {@link ../../../input/paneUiStore.js paneUiStore} keyed by `id`
 * instead of component state, so it survives the controller unmounting/remounting. Returns the same
 * `[value, setValue]` tuple as `useState`, including the functional-updater form; callbacks read the
 * current value off the store handle (not a stale render closure). The stored value is raw —
 * `laneIndex` is clamped on read against the live `laneCount`.
 */
export function usePaneTransitCursor(
  id: string,
  laneCount: number,
): [TransitCursor, Dispatch<SetStateAction<TransitCursor>>] {
  const paneUi: PaneUiStoreApi = useInputStores().paneUi;
  const cursorState = usePaneUiStore((s) => s.transitCursors[id] ?? DEFAULT_TRANSIT_CURSOR);
  const cursor = clampTransitCursor(cursorState, laneCount);

  const setCursor: Dispatch<SetStateAction<TransitCursor>> = useCallback(
    (action) => {
      const current = paneUi.getState().transitCursors[id] ?? DEFAULT_TRANSIT_CURSOR;
      const next = typeof action === 'function' ? action(current) : action;
      paneUi.getState().setTransitCursor(id, next);
    },
    [paneUi, id],
  );

  return [cursor, setCursor];
}
