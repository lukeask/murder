/**
 * Workspace next / prev / jump helpers for the web shell.
 * Thin wrappers over ui-core `switchWorkspace` — no frame capture (web uses CSS flash instead).
 *
 * Also bridges web `panelFocusStore` ↔ ui-core `focusStore.intendedId` so rail focus participates
 * in the snapshot pipeline (serialize before switch, hydrate after).
 */

import { CHAT_FOCUS, type FocusId } from '@murder/ui-core/input/focusIds.js';
import { PANEL_IDS, type PanelId } from '@murder/ui-core/input/panels.js';
import {
  applyWorkspaceCount as applyWorkspaceCountCore,
  switchWorkspace,
  type WorkspaceStores,
} from '@murder/ui-core/input/workspaceSwitch.js';
import { panelFocusStore, type FocusablePanelId } from '../panelFocus.js';

function isPanelId(id: string): id is PanelId {
  return (PANEL_IDS as readonly string[]).includes(id);
}

/** Push live rail focus into `focus.intendedId` so serialize captures it. */
export function syncPanelFocusIntoFocusStore(stores: WorkspaceStores): void {
  const panelId = panelFocusStore.getState().focusedId;
  if (panelId === null || panelId === 'settings') {
    stores.focus.getState().focus(CHAT_FOCUS);
    return;
  }
  stores.focus.getState().focus(panelId);
}

/** Pull snapshotted `focus.intendedId` into the web rail focus store after hydrate. */
export function syncFocusStoreIntoPanelFocus(stores: WorkspaceStores): void {
  const intended: FocusId = stores.focus.getState().intendedId;
  if (isPanelId(intended)) {
    panelFocusStore.getState().focus(intended as FocusablePanelId);
    return;
  }
  panelFocusStore.getState().clear();
}

function switchWorkspaceWeb(
  stores: WorkspaceStores,
  targetIndex: number,
  direction: 'next' | 'prev',
): void {
  syncPanelFocusIntoFocusStore(stores);
  switchWorkspace(stores, targetIndex, direction);
  syncFocusStoreIntoPanelFocus(stores);
}

/** Cycle forward (wrapping). */
export function workspaceNext(stores: WorkspaceStores): void {
  const ws = stores.workspace.getState();
  if (ws.count <= 1) return;
  const target = (ws.activeIndex + 1) % ws.count;
  switchWorkspaceWeb(stores, target, 'next');
}

/** Cycle backward (wrapping). */
export function workspacePrev(stores: WorkspaceStores): void {
  const ws = stores.workspace.getState();
  if (ws.count <= 1) return;
  const target = (ws.activeIndex - 1 + ws.count) % ws.count;
  switchWorkspaceWeb(stores, target, 'prev');
}

/** Jump to 0-based index (no-op when out of range or already active). */
export function workspaceJump(stores: WorkspaceStores, index: number): void {
  const ws = stores.workspace.getState();
  const direction = index > ws.activeIndex ? 'next' : 'prev';
  switchWorkspaceWeb(stores, index, direction);
}

/**
 * React to `workspace_count` settings changes — wraps ui-core `applyWorkspaceCount` and
 * re-syncs rail focus when a shrink clamp hydrates a surviving slot.
 */
export function applyWorkspaceCount(stores: WorkspaceStores, count: number): void {
  applyWorkspaceCountCore(stores, count);
  syncFocusStoreIntoPanelFocus(stores);
}
