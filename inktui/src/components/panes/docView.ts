import { useCallback } from 'react';
import { useAppStore, useAppStoreApi } from '../../hooks/useAppStore.js';
import { useInputStores } from '../../hooks/useInputStores.js';
import type { StagePaneId } from '../../input/focusStore.js';
import type { DocKind } from '../../store/docView/docViewSlice.js';

/** The focus id for an open document pane. This is the single adapter-level owner of the
 * `stage:doc:` scheme so panel/list callers do not depend on the legacy DocPane module. */
export function docPaneFocusId(name: string): StagePaneId {
  return `stage:doc:${name}`;
}

/**
 * Hook for doc panels (Plans/Notes/Reports). Returns a `toggleDoc(name)` callback that opens the
 * document through the docView slice and moves focus to the mounted document pane, or closes the
 * currently open matching document.
 */
export function useDocView(kind: DocKind): (name: string) => void {
  const { focus } = useInputStores();
  const store = useAppStoreApi();
  const openAction = useAppStore((s) => s.actions.docView.open);
  const closeAction = useAppStore((s) => s.actions.docView.close);

  return useCallback(
    (name: string) => {
      const current = store.getState().docView.open;
      if (current !== null && current.kind === kind && current.name === name) {
        closeAction();
        return;
      }
      void openAction(kind, name);
      focus.getState().focus(docPaneFocusId(name));
    },
    [focus, store, kind, openAction, closeAction],
  );
}
