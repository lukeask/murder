/**
 * Store-backed history filter mode — paneUiStore, keyed by pane id (workspace-switch safe).
 */

import { useCallback, type Dispatch, type SetStateAction } from 'react';
import { useStoreWithEqualityFn } from 'zustand/traditional';
import type { PaneUiStoreApi } from '@murder/ui-core/input/paneUiStore.js';
import type { HistoryMode } from '@murder/ui-core/selectors/historySelectors.js';
import { useComposerStores } from './ComposerStoresProvider.js';

const DEFAULT_HISTORY_MODE: HistoryMode = 'loose';

/** `[mode, setMode]` for the history pane. */
export function usePaneHistoryMode(
  id: string,
): [HistoryMode, Dispatch<SetStateAction<HistoryMode>>] {
  const { paneUi } = useComposerStores();
  const mode = useStoreWithEqualityFn(
    paneUi,
    (s) => s.historyModes[id] ?? DEFAULT_HISTORY_MODE,
  );

  const setMode: Dispatch<SetStateAction<HistoryMode>> = useCallback(
    (action) => {
      const api: PaneUiStoreApi = paneUi;
      const current = api.getState().historyModes[id] ?? DEFAULT_HISTORY_MODE;
      const next = typeof action === 'function' ? action(current) : action;
      api.getState().setHistoryMode(id, next);
    },
    [paneUi, id],
  );

  return [mode, setMode];
}
