/**
 * Web cockpit rail visibility helpers over the composer-bundle `panelStore`.
 * Disk persistence is not added — TUI `panelStore` itself does not persist (workspace snapshots do).
 */

import { useStoreWithEqualityFn } from 'zustand/traditional';
import type { PanelStoreApi, PanelState } from '@murder/ui-core/input/panelStore.js';
import type { PanelId } from '@murder/ui-core/input/panels.js';
import { useComposerStores } from './composer/ComposerStoresProvider.js';
import { panelFocusStore } from './panelFocus.js';

/** Subscribe to whether a rail panel is toggled on. */
export function usePanelIsVisible(id: PanelId): boolean {
  const { panels } = useComposerStores();
  return useStoreWithEqualityFn(panels, (s: PanelState) => s.visible.has(id));
}

/**
 * Digit-chord semantics (TUI `togglePanelFromShortcut` without full focus geometry):
 * hidden → show + focus + scroll into view; visible → hide (clear focus if this panel held it).
 */
export function togglePanelVisibility(panels: PanelStoreApi, id: PanelId): void {
  const state = panels.getState();
  const focus = panelFocusStore.getState();
  if (!state.visible.has(id)) {
    state.show(id);
    focus.focus(id);
    queueMicrotask(() => {
      document.querySelector(`[data-panel-id="${id}"]`)?.scrollIntoView({
        block: 'nearest',
        behavior: 'smooth',
      });
    });
    return;
  }
  state.hide(id);
  if (focus.focusedId === id) {
    focus.clear();
  }
}
