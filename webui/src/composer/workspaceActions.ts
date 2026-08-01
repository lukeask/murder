/**
 * Workspace next / prev / jump helpers for the web shell.
 * Thin wrappers over ui-core `switchWorkspace` — no frame capture (web uses CSS flash instead).
 */

import {
  switchWorkspace,
  type WorkspaceStores,
} from '@murder/ui-core/input/workspaceSwitch.js';

/** Cycle forward (wrapping). */
export function workspaceNext(stores: WorkspaceStores): void {
  const ws = stores.workspace.getState();
  if (ws.count <= 1) return;
  const target = (ws.activeIndex + 1) % ws.count;
  switchWorkspace(stores, target, 'next');
}

/** Cycle backward (wrapping). */
export function workspacePrev(stores: WorkspaceStores): void {
  const ws = stores.workspace.getState();
  if (ws.count <= 1) return;
  const target = (ws.activeIndex - 1 + ws.count) % ws.count;
  switchWorkspace(stores, target, 'prev');
}

/** Jump to 0-based index (no-op when out of range or already active). */
export function workspaceJump(stores: WorkspaceStores, index: number): void {
  const ws = stores.workspace.getState();
  const direction = index > ws.activeIndex ? 'next' : 'prev';
  switchWorkspace(stores, index, direction);
}
