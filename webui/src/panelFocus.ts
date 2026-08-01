/**
 * Thin rail-panel keyboard focus for the web cockpit.
 * Independent of TUI focusStore / focusGraph — click header / digit-show sets focus; Esc / chat clears.
 * Modifier+h/j/k/l hops via DOM getBoundingClientRect nearest-neighbor (ui-core geometry kernel).
 */

import { createStore } from 'zustand/vanilla';
import { useStoreWithEqualityFn } from 'zustand/traditional';
import type { PanelId } from '@murder/ui-core/input/panels.js';
import {
  directionalFocusTarget,
  type Direction,
  type FocusCandidate,
  type Rect,
} from '@murder/ui-core/input/geometry.js';

export type FocusablePanelId = PanelId | 'settings';

/** Synthetic focus id for the center stage (chat / transcripts) in geometry hops. */
export const STAGE_FOCUS_ID = 'stage' as const;

export type GeometryFocusId = FocusablePanelId | typeof STAGE_FOCUS_ID;

type PanelFocusState = {
  readonly focusedId: FocusablePanelId | null;
  focus(id: FocusablePanelId): void;
  clear(): void;
};

/**
 * Rail panel keyboard focus. Workspace-scoped via sync with `focusStore.intendedId` in
 * {@link ./composer/workspaceActions.js} (serialize before switch, hydrate after).
 */
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

const VIM_DIR: Readonly<Record<string, Direction>> = {
  h: 'left',
  l: 'right',
  k: 'up',
  j: 'down',
};

function clientRectToFocusRect(r: DOMRect): Rect {
  return { x: r.left, y: r.top, width: r.width, height: r.height };
}

/**
 * Visible focus candidates: every `[data-panel-id]` in the DOM plus the stage
 * (`[data-focus-id="stage"]`).
 */
export function collectGeometryFocusCandidates(): FocusCandidate<GeometryFocusId>[] {
  const out: FocusCandidate<GeometryFocusId>[] = [];
  for (const el of document.querySelectorAll('[data-panel-id]')) {
    if (!(el instanceof HTMLElement)) continue;
    const id = el.getAttribute('data-panel-id');
    if (id === null || id === '') continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    out.push({ id: id as GeometryFocusId, rect: clientRectToFocusRect(r) });
  }
  const stage = document.querySelector('[data-focus-id="stage"]');
  if (stage instanceof HTMLElement) {
    const r = stage.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) {
      out.push({ id: STAGE_FOCUS_ID, rect: clientRectToFocusRect(r) });
    }
  }
  return out;
}

/** Current geometry focus: rail panel if set, otherwise stage. */
export function currentGeometryFocusId(): GeometryFocusId {
  return panelFocusStore.getState().focusedId ?? STAGE_FOCUS_ID;
}

/**
 * Move keyboard focus to the nearest visible panel/stage neighbour in `direction`.
 * Returns the new id, or null when nothing lies that way.
 */
export function hopPanelFocus(direction: Direction): GeometryFocusId | null {
  const candidates = collectGeometryFocusCandidates();
  const current = currentGeometryFocusId();
  if (!candidates.some((c) => c.id === current)) {
    // Source missing from DOM (e.g. panel just hidden) — treat stage as home when present.
    const fallback = candidates.find((c) => c.id === STAGE_FOCUS_ID)?.id ?? candidates[0]?.id;
    if (fallback === undefined) return null;
    applyGeometryFocus(fallback);
    return fallback;
  }
  const next = directionalFocusTarget(direction, current, candidates);
  if (next === null) return null;
  applyGeometryFocus(next);
  return next;
}

function applyGeometryFocus(id: GeometryFocusId): void {
  if (id === STAGE_FOCUS_ID) {
    panelFocusStore.getState().clear();
    return;
  }
  panelFocusStore.getState().focus(id);
  const el = document.querySelector(`[data-panel-id="${id}"]`);
  if (el instanceof HTMLElement && typeof el.scrollIntoView === 'function') {
    el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
}

/** Resolve vim letter → direction for modifier+h/j/k/l. */
export function directionFromVimKey(key: string): Direction | null {
  return VIM_DIR[key] ?? null;
}
