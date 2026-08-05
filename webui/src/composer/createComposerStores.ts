/**
 * WebUI input-store bundle — chat composer + workspace-switch pipeline stores.
 * Same ui-core stores as the TUI; no inktui imports.
 *
 * {@link WorkspaceStores} needs panels / focus / paneUi / workspace / chatInput / app.
 * Chat history + vim stay global (never snapshotted).
 */

import { EMPTY_BUFFER } from '@murder/ui-core/input/chatBuffer.js';
import { createChatHistoryStore, type ChatHistoryStoreApi } from '@murder/ui-core/input/chatHistoryStore.js';
import { createChatInputStore, type ChatInputStoreApi } from '@murder/ui-core/input/chatInputStore.js';
import { createChatVimStore, type ChatVimStoreApi } from '@murder/ui-core/input/chatVimStore.js';
import { CHAT_FOCUS } from '@murder/ui-core/input/focusIds.js';
import { createFocusStore, type FocusStoreApi } from '@murder/ui-core/input/focusStore.js';
import { createPanelStore, type PanelStoreApi } from '@murder/ui-core/input/panelStore.js';
import { PANEL_IDS } from '@murder/ui-core/input/panels.js';
import { createPaneUiStore, type PaneUiStoreApi } from '@murder/ui-core/input/paneUiStore.js';
import {
  createWorkspaceStore,
  type WorkspaceSnapshot,
  type WorkspaceStoreApi,
} from '@murder/ui-core/input/workspaceStore.js';
import type { AppStoreApi } from '@murder/ui-core/store/store.js';
import type { WorkspaceStores } from '@murder/ui-core/input/workspaceSwitch.js';

export type ComposerStores = {
  readonly chatInput: ChatInputStoreApi;
  readonly chatHistory: ChatHistoryStoreApi;
  readonly chatVim: ChatVimStoreApi;
  readonly panels: PanelStoreApi;
  readonly focus: FocusStoreApi;
  readonly paneUi: PaneUiStoreApi;
  readonly workspace: WorkspaceStoreApi;
};

/**
 * Build the web input-store bundle (shared across the shell).
 * Rails start with every {@link PANEL_IDS} panel visible (web boots full rails; TUI starts empty).
 */
export function createComposerStores(): ComposerStores {
  const panels = createPanelStore(PANEL_IDS);
  const focus = createFocusStore(panels);
  return {
    chatInput: createChatInputStore(),
    chatHistory: createChatHistoryStore(),
    chatVim: createChatVimStore(),
    panels,
    focus,
    paneUi: createPaneUiStore(),
    workspace: createWorkspaceStore(),
  };
}

/** Project the composer bundle + app store into the switch-pipeline handle. */
export function toWorkspaceStores(stores: ComposerStores, app: AppStoreApi): WorkspaceStores {
  return {
    workspace: stores.workspace,
    panels: stores.panels,
    focus: stores.focus,
    chatInput: stores.chatInput,
    paneUi: stores.paneUi,
    app,
  };
}

/**
 * Cold-open layout for a never-parked repository — full rails matching {@link createComposerStores}.
 * Passed as `freshSnapshot` so resume does not hydrate the TUI chat-only null default.
 */
export function webFreshWorkspaceSnapshot(): WorkspaceSnapshot {
  return {
    panelsVisible: [...PANEL_IDS],
    focusIntendedId: CHAT_FOCUS,
    paneUi: {
      cursors: {},
      scrolls: {},
      expandeds: {},
      historyModes: {},
      gotoLines: {},
      transitCursors: {},
      gBuffers: {},
    },
    chatInput: { buffer: EMPTY_BUFFER, historyIndex: null, stashedDraft: null },
    conversations: {
      activePaneAgentId: null,
      paneOverrides: {},
      paneReapAges: {},
      paneViewModes: {},
    },
    docView: null,
  };
}
