import type { Dispatch, SetStateAction } from 'react';
import { useCallback } from 'react';
import { useInputStores, usePaneUiStore } from '../../../hooks/useInputStores.js';
import type { PaneUiStoreApi } from '../../../input/paneUiStore.js';

/**
 * Store-backed drop-in for `const [expanded, setExpanded] = useState(false)` in a pane — the toggle
 * lives in {@link ../../../input/paneUiStore.js paneUiStore} keyed by `id` instead of component
 * state, so a pane's expanded state survives the controller unmounting/remounting (panel toggle,
 * workspace switch). Returns the same `[value, setValue]` tuple as `useState<boolean>`, including the
 * functional-updater form; callbacks read the current value off the store handle (not a stale render
 * closure).
 */
export function usePaneExpandedState(id: string): [boolean, Dispatch<SetStateAction<boolean>>] {
  const paneUi: PaneUiStoreApi = useInputStores().paneUi;
  const expanded = usePaneUiStore((s) => s.expandeds[id] ?? false);

  const setExpanded: Dispatch<SetStateAction<boolean>> = useCallback(
    (action) => {
      const current = paneUi.getState().expandeds[id] ?? false;
      const next = typeof action === 'function' ? action(current) : action;
      paneUi.getState().setExpanded(id, next);
    },
    [paneUi, id],
  );

  return [expanded, setExpanded];
}
