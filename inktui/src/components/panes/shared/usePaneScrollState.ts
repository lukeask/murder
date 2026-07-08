import type { Dispatch, SetStateAction } from 'react';
import { useCallback } from 'react';
import { useInputStores, usePaneUiStore } from '../../../hooks/useInputStores.js';
import type { PaneUiStoreApi } from '../../../input/paneUiStore.js';

/**
 * Store-backed drop-in for `const [scroll, setScroll] = useState(0)` in a scroll pane — the offset
 * lives in {@link ../../../input/paneUiStore.js paneUiStore} keyed by `id` instead of component
 * state, so a pane's scroll position survives the controller unmounting/remounting (panel toggle,
 * workspace switch). Returns the same `[value, setValue]` tuple as `useState<number>`, including the
 * functional-updater form; callbacks read the current value off the store handle (not a stale render
 * closure). The stored value is raw — callers clamp on read against their live window, exactly as
 * they did with the `useState` value.
 */
export function usePaneScrollState(id: string): [number, Dispatch<SetStateAction<number>>] {
  const paneUi: PaneUiStoreApi = useInputStores().paneUi;
  const scroll = usePaneUiStore((s) => s.scrolls[id] ?? 0);

  const setScroll: Dispatch<SetStateAction<number>> = useCallback(
    (action) => {
      const current = paneUi.getState().scrolls[id] ?? 0;
      const next = typeof action === 'function' ? action(current) : action;
      paneUi.getState().setScroll(id, next);
    },
    [paneUi, id],
  );

  return [scroll, setScroll];
}
