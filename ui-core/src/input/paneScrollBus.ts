/**
 * `paneScrollBus` — a tiny focus-id-keyed command channel for "scroll this pane by N" requests.
 *
 * ## Why a bus and not a store
 *
 * A Stage pane (committed transcript, doc) owns its scroll offset as local `useState` (rule 1 — the window
 * offset is panel-local, not a slice). Keyboard scroll (`j`/`k`) flows through the pane's own keymap,
 * so it never needs to reach in from outside. The mouse wheel is different: it can target a pane that
 * is NOT focused — when the chat INPUT holds focus, the wheel should scroll the input's active-target
 * history pane, which the input is not. There is no key event to route, and lifting every pane's
 * offset into a shared store just to nudge one from outside would be a large, churny refactor of code
 * that is correct as-is.
 *
 * So this is a command channel, not state: {@link useRootInput} resolves which pane the wheel targets
 * and {@link PaneScrollBus.emit}s a nudge at that pane's {@link FocusId}; the pane {@link
 * PaneScrollBus.subscribe}s for its own id and applies the delta to its local offset (clamped to its
 * own scroll range, which only the pane knows). Keyed by `FocusId` so both transcript panes
 * and doc panes use the one mechanism. Framework-
 * agnostic (rule 4): a plain emitter, no React, no Ink — the component layer binds it via an effect.
 */

import type { FocusId } from './focusStore.js';

/** The scroll direction of a wheel notch. `up` = reveal older/earlier content, `down` = newer/later. */
export type ScrollDirection = 'up' | 'down';

/** A subscriber's handler: a request to scroll by `amount` lines in `direction`. The pane clamps. */
export type PaneScrollListener = (direction: ScrollDirection, amount: number) => void;
export type TerminalViewportAction =
  | {
      readonly kind: 'pan';
      readonly deltaColumns: number;
      readonly deltaRows: number;
    }
  | { readonly kind: 'follow_cursor' };
export type TerminalViewportListener = (action: TerminalViewportAction) => void;

/** The command channel. One instance lives in the input-store bundle. */
export interface PaneScrollBus {
  /** Request a scroll of the pane registered at `focusId`. A no-op if no pane is subscribed there
   * (e.g. the target pane isn't mounted) — callers may emit optimistically. */
  emit(focusId: FocusId, direction: ScrollDirection, amount: number): void;
  /** Subscribe a pane's scroll handler for its own focus id; returns an unsubscribe fn for the
   * mount/unmount effect. A pane subscribes unconditionally (NOT gated on focus) so the wheel can
   * drive it while the chat input — not the pane — holds focus. */
  subscribe(focusId: FocusId, listener: PaneScrollListener): () => void;
  /** Send an explicit local terminal-viewport command without mutating terminal geometry. */
  emitTerminalViewport(focusId: FocusId, action: TerminalViewportAction): void;
  /** Subscribe an embedded terminal surface to local pan/follow commands. */
  subscribeTerminalViewport(focusId: FocusId, listener: TerminalViewportListener): () => void;
}

/** Build a pane-scroll bus. Listeners are kept in a per-focus-id set so emit is a direct fan-out with
 * no scan; empty sets are pruned so the map doesn't accumulate ids for unmounted panes. */
export function createPaneScrollBus(): PaneScrollBus {
  const listeners = new Map<FocusId, Set<PaneScrollListener>>();
  const viewportListeners = new Map<FocusId, Set<TerminalViewportListener>>();
  return {
    emit(focusId, direction, amount) {
      const set = listeners.get(focusId);
      if (set === undefined) {
        return;
      }
      for (const listener of set) {
        listener(direction, amount);
      }
    },
    subscribe(focusId, listener) {
      let set = listeners.get(focusId);
      if (set === undefined) {
        set = new Set();
        listeners.set(focusId, set);
      }
      set.add(listener);
      return () => {
        const current = listeners.get(focusId);
        if (current === undefined) {
          return;
        }
        current.delete(listener);
        if (current.size === 0) {
          listeners.delete(focusId);
        }
      };
    },
    emitTerminalViewport(focusId, action) {
      for (const listener of viewportListeners.get(focusId) ?? []) listener(action);
    },
    subscribeTerminalViewport(focusId, listener) {
      let set = viewportListeners.get(focusId);
      if (set === undefined) {
        set = new Set();
        viewportListeners.set(focusId, set);
      }
      set.add(listener);
      return () => {
        const current = viewportListeners.get(focusId);
        current?.delete(listener);
        if (current?.size === 0) viewportListeners.delete(focusId);
      };
    },
  };
}
