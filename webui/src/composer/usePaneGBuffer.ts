/**
 * Store-backed `g`-jump buffer for the tree panel — paneUiStore, no inktui.
 */

import { useCallback, type Dispatch, type SetStateAction } from 'react';
import { useStoreWithEqualityFn } from 'zustand/traditional';
import type { PaneUiStoreApi } from '@murder/ui-core/input/paneUiStore.js';
import { useComposerStores } from './ComposerStoresProvider.js';

/** `[gBuffer, setGBuffer]` — `null` means not in `g` mode. */
export function usePaneGBuffer(
  id: string,
): [string | null, Dispatch<SetStateAction<string | null>>] {
  const { paneUi } = useComposerStores();
  const gBuffer = useStoreWithEqualityFn(paneUi, (s) => s.gBuffers[id] ?? null);

  const setGBuffer: Dispatch<SetStateAction<string | null>> = useCallback(
    (action) => {
      const api: PaneUiStoreApi = paneUi;
      const current = api.getState().gBuffers[id] ?? null;
      const next = typeof action === 'function' ? action(current) : action;
      api.getState().setGBuffer(id, next);
    },
    [paneUi, id],
  );

  return [gBuffer, setGBuffer];
}
