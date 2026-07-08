import type { Dispatch, SetStateAction } from 'react';
import { useCallback } from 'react';
import { useInputStores, usePaneUiStore } from '../../../hooks/useInputStores.js';
import type { PaneUiStoreApi } from '../../../input/paneUiStore.js';
import type { HistoryMode } from '../../../selectors/historySelectors.js';

const DEFAULT_HISTORY_MODE: HistoryMode = 'loose';

/**
 * Store-backed drop-in for `const [mode, setMode] = useState<HistoryMode>('loose')` in the history
 * pane — the filter mode lives in {@link ../../../input/paneUiStore.js paneUiStore} keyed by `id`
 * instead of component state, so a pane's mode survives the controller unmounting/remounting (panel
 * toggle, workspace switch). Returns the same `[value, setValue]` tuple as `useState<HistoryMode>`,
 * including the functional-updater form; callbacks read the current value off the store handle (not a
 * stale render closure).
 */
export function usePaneHistoryMode(
  id: string,
): [HistoryMode, Dispatch<SetStateAction<HistoryMode>>] {
  const paneUi: PaneUiStoreApi = useInputStores().paneUi;
  const mode = usePaneUiStore((s) => s.historyModes[id] ?? DEFAULT_HISTORY_MODE);

  const setMode: Dispatch<SetStateAction<HistoryMode>> = useCallback(
    (action) => {
      const current = paneUi.getState().historyModes[id] ?? DEFAULT_HISTORY_MODE;
      const next = typeof action === 'function' ? action(current) : action;
      paneUi.getState().setHistoryMode(id, next);
    },
    [paneUi, id],
  );

  return [mode, setMode];
}
