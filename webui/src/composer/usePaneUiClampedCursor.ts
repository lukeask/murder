/**
 * Store-backed list cursor — paneUiStore, keyed by pane id (workspace-switch safe).
 */

import { useCallback, type Dispatch, type SetStateAction } from 'react';
import { useStoreWithEqualityFn } from 'zustand/traditional';
import type { PaneUiStoreApi } from '@murder/ui-core/input/paneUiStore.js';
import { useComposerStores } from './ComposerStoresProvider.js';

function clampCursor(cursor: number, rowCount: number): number {
  return Math.min(Math.max(cursor, 0), Math.max(rowCount - 1, 0));
}

/** `[cursor, setCursor]` for pane id — clamped on read against live row count. */
export function usePaneUiClampedCursor(
  id: string,
  rowCount: number,
): [number, Dispatch<SetStateAction<number>>] {
  const { paneUi } = useComposerStores();
  const cursorState = useStoreWithEqualityFn(paneUi, (s) => s.cursors[id] ?? 0);
  const cursor = clampCursor(cursorState, rowCount);

  const setCursor: Dispatch<SetStateAction<number>> = useCallback(
    (action) => {
      const api: PaneUiStoreApi = paneUi;
      const current = api.getState().cursors[id] ?? 0;
      const next = typeof action === 'function' ? action(current) : action;
      api.getState().setCursor(id, next);
    },
    [paneUi, id],
  );

  return [cursor, setCursor];
}
