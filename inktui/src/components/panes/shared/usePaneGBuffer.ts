import type { Dispatch, SetStateAction } from 'react';
import { useCallback } from 'react';
import { useInputStores, usePaneUiStore } from '../../../hooks/useInputStores.js';
import type { PaneUiStoreApi } from '../../../input/paneUiStore.js';

/**
 * Store-backed drop-in for `const [gBuffer, setGBuffer] = useState<string | null>(null)` in the
 * tree pane — the buffer lives in {@link ../../../input/paneUiStore.js paneUiStore} keyed by `id`
 * instead of component state, so it survives the controller unmounting/remounting. Returns the same
 * `[value, setValue]` tuple as `useState`, including the functional-updater form; callbacks read the
 * current value off the store handle (not a stale render closure).
 */
export function usePaneGBuffer(
  id: string,
): [string | null, Dispatch<SetStateAction<string | null>>] {
  const paneUi: PaneUiStoreApi = useInputStores().paneUi;
  const gBuffer = usePaneUiStore((s) => s.gBuffers[id] ?? null);

  const setGBuffer: Dispatch<SetStateAction<string | null>> = useCallback(
    (action) => {
      const current = paneUi.getState().gBuffers[id] ?? null;
      const next = typeof action === 'function' ? action(current) : action;
      paneUi.getState().setGBuffer(id, next);
    },
    [paneUi, id],
  );

  return [gBuffer, setGBuffer];
}
