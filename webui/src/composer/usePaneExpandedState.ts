/**
 * Store-backed expanded/compact toggle — paneUiStore, keyed by pane id (workspace-switch safe).
 */

import { useCallback, type Dispatch, type SetStateAction } from 'react';
import { useStoreWithEqualityFn } from 'zustand/traditional';
import type { PaneUiStoreApi } from '@murder/ui-core/input/paneUiStore.js';
import { useComposerStores } from './ComposerStoresProvider.js';

/**
 * `[expanded, setExpanded]` for pane id.
 * @param defaultExpanded — used when the id has never been written (web crows default `true`).
 */
export function usePaneExpandedState(
  id: string,
  defaultExpanded: boolean = false,
): [boolean, Dispatch<SetStateAction<boolean>>] {
  const { paneUi } = useComposerStores();
  const expanded = useStoreWithEqualityFn(
    paneUi,
    (s) => s.expandeds[id] ?? defaultExpanded,
  );

  const setExpanded: Dispatch<SetStateAction<boolean>> = useCallback(
    (action) => {
      const api: PaneUiStoreApi = paneUi;
      const current = api.getState().expandeds[id] ?? defaultExpanded;
      const next = typeof action === 'function' ? action(current) : action;
      api.getState().setExpanded(id, next);
    },
    [paneUi, id, defaultExpanded],
  );

  return [expanded, setExpanded];
}
