/**
 * Store-backed scroll offset — paneUiStore, keyed by pane id (workspace-switch safe).
 * Web stores DOM `scrollTop` (TUI stores line offsets under the same map).
 */

import { useCallback, type Dispatch, type SetStateAction } from 'react';
import { useStoreWithEqualityFn } from 'zustand/traditional';
import type { PaneUiStoreApi } from '@murder/ui-core/input/paneUiStore.js';
import { useComposerStores } from './ComposerStoresProvider.js';

/** `[scroll, setScroll]` for pane id — raw value; callers clamp against the live window. */
export function usePaneScrollState(id: string): [number, Dispatch<SetStateAction<number>>] {
  const { paneUi } = useComposerStores();
  const scroll = useStoreWithEqualityFn(paneUi, (s) => s.scrolls[id] ?? 0);

  const setScroll: Dispatch<SetStateAction<number>> = useCallback(
    (action) => {
      const api: PaneUiStoreApi = paneUi;
      const current = api.getState().scrolls[id] ?? 0;
      const next = typeof action === 'function' ? action(current) : action;
      api.getState().setScroll(id, next);
    },
    [paneUi, id],
  );

  return [scroll, setScroll];
}
