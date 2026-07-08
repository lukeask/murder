import type { Dispatch, SetStateAction } from 'react';
import { useCallback } from 'react';
import { useInputStores, usePaneUiStore } from '../../../hooks/useInputStores.js';
import type { PaneUiStoreApi } from '../../../input/paneUiStore.js';

/**
 * Store-backed drop-in for `const [gotoLine, setGotoLine] = useState<number | null>(null)` in a
 * scroll pane — the pending goto-line lives in {@link ../../../input/paneUiStore.js paneUiStore}
 * keyed by `id` instead of component state, so a pane's goto survives the controller
 * unmounting/remounting (panel toggle, workspace switch). Returns the same `[value, setValue]`
 * tuple as `useState<number | null>`, including the functional-updater form; callbacks read the
 * current value off the store handle (not a stale render closure).
 */
export function usePaneGotoLineState(
  id: string,
): [number | null, Dispatch<SetStateAction<number | null>>] {
  const paneUi: PaneUiStoreApi = useInputStores().paneUi;
  const gotoLine = usePaneUiStore((s) => s.gotoLines[id] ?? null);

  const setGotoLine: Dispatch<SetStateAction<number | null>> = useCallback(
    (action) => {
      const current = paneUi.getState().gotoLines[id] ?? null;
      const next = typeof action === 'function' ? action(current) : action;
      paneUi.getState().setGotoLine(id, next);
    },
    [paneUi, id],
  );

  return [gotoLine, setGotoLine];
}
