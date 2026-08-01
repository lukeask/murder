/**
 * Thin rail-panel keyboard focus for the web cockpit.
 * Independent of TUI focusStore geometry — click header / digit-show sets focus; Esc / chat clears.
 */

import { createStore } from 'zustand/vanilla';
import { useStoreWithEqualityFn } from 'zustand/traditional';
import type { PanelId } from '@murder/ui-core/input/panels.js';

export type FocusablePanelId = PanelId | 'settings';

type PanelFocusState = {
  readonly focusedId: FocusablePanelId | null;
  focus(id: FocusablePanelId): void;
  clear(): void;
};

/** Session-local panel keyboard focus (not snapshotted with workspaces). */
export const panelFocusStore = createStore<PanelFocusState>()((set) => ({
  focusedId: null,
  focus(id) {
    set({ focusedId: id });
  },
  clear() {
    set({ focusedId: null });
  },
}));

/** Subscribe to the focused rail panel id (or null). */
export function useFocusedPanelId(): FocusablePanelId | null {
  return useStoreWithEqualityFn(panelFocusStore, (s) => s.focusedId);
}

/** True when `id` holds rail keyboard focus. */
export function useIsPanelFocused(id: FocusablePanelId): boolean {
  return useStoreWithEqualityFn(panelFocusStore, (s) => s.focusedId === id);
}
