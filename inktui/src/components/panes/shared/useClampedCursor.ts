import type { Dispatch, SetStateAction } from 'react';
import { useCallback, useState } from 'react';
import { useInputStores, usePaneUiStore } from '../../../hooks/useInputStores.js';
import type { PaneUiStoreApi } from '../../../input/paneUiStore.js';

export interface ClampedCursor {
  readonly cursor: number;
  readonly setCursor: Dispatch<SetStateAction<number>>;
  readonly moveDown: () => void;
  readonly moveUp: () => void;
}

function clampCursor(cursor: number, rowCount: number): number {
  return Math.min(Math.max(cursor, 0), Math.max(rowCount - 1, 0));
}

export function useClampedCursor(rowCount: number): ClampedCursor {
  const [cursorState, setCursor] = useState(0);
  const cursor = clampCursor(cursorState, rowCount);

  const moveDown = useCallback(() => {
    setCursor((current) => clampCursor(clampCursor(current, rowCount) + 1, rowCount));
  }, [rowCount]);

  const moveUp = useCallback(() => {
    setCursor((current) => clampCursor(clampCursor(current, rowCount) - 1, rowCount));
  }, [rowCount]);

  return { cursor, setCursor, moveDown, moveUp };
}

/**
 * Store-backed {@link useClampedCursor} — identical behaviour and return shape, but the selection
 * index lives in {@link ../../../input/paneUiStore.js paneUiStore} keyed by `id` instead of in
 * component `useState`, so the pane's cursor survives the controller unmounting/remounting (panel
 * toggle, workspace switch). Drop-in replacement: a pane swaps
 * `useClampedCursor(rowCount)` for `usePaneUiClampedCursor(id, rowCount)` and nothing else changes.
 *
 * As with the `useState` variant, the store holds the raw (unclamped) value and we clamp on read
 * against the live `rowCount`; the callbacks read the current value straight off the store handle
 * (not a stale render closure), matching the functional-update semantics of the original.
 */
export function usePaneUiClampedCursor(id: string, rowCount: number): ClampedCursor {
  const paneUi: PaneUiStoreApi = useInputStores().paneUi;
  const cursorState = usePaneUiStore((s) => s.cursors[id] ?? 0);
  const cursor = clampCursor(cursorState, rowCount);

  const setCursor: Dispatch<SetStateAction<number>> = useCallback(
    (action) => {
      const current = paneUi.getState().cursors[id] ?? 0;
      const next = typeof action === 'function' ? action(current) : action;
      paneUi.getState().setCursor(id, next);
    },
    [paneUi, id],
  );

  const moveDown = useCallback(() => {
    const current = paneUi.getState().cursors[id] ?? 0;
    paneUi.getState().setCursor(id, clampCursor(clampCursor(current, rowCount) + 1, rowCount));
  }, [paneUi, id, rowCount]);

  const moveUp = useCallback(() => {
    const current = paneUi.getState().cursors[id] ?? 0;
    paneUi.getState().setCursor(id, clampCursor(clampCursor(current, rowCount) - 1, rowCount));
  }, [paneUi, id, rowCount]);

  return { cursor, setCursor, moveDown, moveUp };
}
