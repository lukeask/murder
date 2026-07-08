/**
 * Compose the three input stores into one wired bundle — the focus store bound to the panel store
 * it resolves against, plus the keymap registry. The app shell (C5) and the C4 tests both build the
 * bundle through here, so the wiring (focus ← panels) lives in exactly one place.
 */

import { createBindingsStore } from './bindingsStore.js';
import { createChatHistoryStore } from './chatHistoryStore.js';
import { createChatInputStore } from './chatInputStore.js';
import { createChatVimStore } from './chatVimStore.js';
import { createFocusStore, type FocusId } from './focusStore.js';
import { createKeymapRegistry } from './keymapRegistry.js';
import { createModeStore } from './modeStore.js';
import { createPanelStore } from './panelStore.js';
import type { PanelId } from './panels.js';
import { createPaneScrollBus } from './paneScrollBus.js';
import { createPaneUiStore } from './paneUiStore.js';
import { createWorkspaceStore } from './workspaceStore.js';

/** The wired input stores. Matches the `InputStores` context value the React provider carries. */
export interface InputStoreBundle {
  readonly panels: ReturnType<typeof createPanelStore>;
  readonly focus: ReturnType<typeof createFocusStore>;
  readonly keymaps: ReturnType<typeof createKeymapRegistry>;
  readonly modes: ReturnType<typeof createModeStore>;
  readonly chatInput: ReturnType<typeof createChatInputStore>;
  /** Murder-wide sent-message history corpus (chat-input overhaul, user ask #4). */
  readonly chatHistory: ReturnType<typeof createChatHistoryStore>;
  /** Vim editing mode state + murder-wide yank register (chat-input overhaul, user ask #3). */
  readonly chatVim: ReturnType<typeof createChatVimStore>;
  readonly bindings: ReturnType<typeof createBindingsStore>;
  /** Focus-id-keyed mouse-wheel scroll command channel (Stage panes subscribe; the root input loop
   * emits to the focused/targeted pane). */
  readonly paneScroll: ReturnType<typeof createPaneScrollBus>;
  /** Per-pane ephemeral UI state (scroll/cursor) keyed by pane id, hoisted out of controller
   * `useState` so it survives pane remount (workspaces plan, step 1). */
  readonly paneUi: ReturnType<typeof createPaneUiStore>;
  /** N-virtual-workspaces slots + active index (workspaces plan, step 2a). Inert at count 1. */
  readonly workspace: ReturnType<typeof createWorkspaceStore>;
}

/** Build the bundle. `initialVisible` seeds the toggled-on panels; `initialFocus` seeds intended
 * focus (defaults to chat — the always-present home). The mode store starts empty (no mode up) and
 * is bound to the focus store so its enter/exit saves+restores focus. The chat-input buffer (C11)
 * starts empty. */
export function createInputStores(
  initialVisible: Iterable<PanelId> = [],
  initialFocus?: FocusId,
): InputStoreBundle {
  const panels = createPanelStore(initialVisible);
  const focus = createFocusStore(panels, initialFocus);
  const keymaps = createKeymapRegistry();
  const modes = createModeStore(focus);
  const chatInput = createChatInputStore();
  // Murder-wide history corpus + vim state: one instance each so send-history recall and the yank
  // register span every recipient target (yank in one crow's draft, paste into another's).
  const chatHistory = createChatHistoryStore();
  const chatVim = createChatVimStore();
  // The bindings store starts at today's behavior (alt modifier, ctrl unavailable, no overrides); a
  // later settings phase mutates it from the settings RPC bridge.
  const bindings = createBindingsStore();
  // The wheel→scroll command channel. Stateless fan-out; one instance so every pane and the root
  // input loop share the same bus.
  const paneScroll = createPaneScrollBus();
  // Per-pane UI state (scroll/cursor). One instance so a pane's position is remembered across the
  // controller unmounting/remounting (panel toggle, workspace switch).
  const paneUi = createPaneUiStore();
  // Workspace slots. Starts at count 1 (feature inert); the settings bridge (step 2c) pushes
  // `workspace_count` through `applyWorkspaceCount`.
  const workspace = createWorkspaceStore();
  return {
    panels,
    focus,
    keymaps,
    modes,
    chatInput,
    chatHistory,
    chatVim,
    bindings,
    paneScroll,
    paneUi,
    workspace,
  };
}
