/**
 * Per-repository workspace bag tests (single-daemon Phase 8).
 *
 * Cookbook: switch repo A→B→A restores A's layout and count; B can have a different count;
 * workspace_count persists in injectable storage per repo.
 */

import { beforeEach, describe, expect, it } from 'vitest';
import { FakeApplicationClient } from '@murder/ui-core/application/FakeApplicationClient.js';
import { EMPTY_BUFFER } from '@murder/ui-core/input/chatBuffer.js';
import { createChatInputStore } from '@murder/ui-core/input/chatInputStore.js';
import { CHAT_FOCUS } from '@murder/ui-core/input/focusIds.js';
import { createFocusStore } from '@murder/ui-core/input/focusStore.js';
import { createPaneUiStore } from '@murder/ui-core/input/paneUiStore.js';
import { createPanelStore } from '@murder/ui-core/input/panelStore.js';
import {
  createWorkspaceStore,
  workspaceCountStorageKey,
  type WorkspaceCountStorage,
  type WorkspaceSnapshot,
} from '@murder/ui-core/input/workspaceStore.js';
import {
  applyWorkspaceCount,
  parkRepositoryWorkspace,
  resumeRepositoryWorkspace,
  switchRepositoryWorkspace,
  type WorkspaceStores,
} from '@murder/ui-core/input/workspaceSwitch.js';
import { createAppStore } from '@murder/ui-core/store/store.js';

function memoryStorage(): WorkspaceCountStorage & { readonly data: Map<string, string> } {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => {
      data.set(key, value);
    },
  };
}

function makeStores(): { stores: WorkspaceStores; dispose: () => void } {
  const panels = createPanelStore();
  const focus = createFocusStore(panels);
  const chatInput = createChatInputStore();
  const paneUi = createPaneUiStore();
  const workspace = createWorkspaceStore();
  const { store: app, dispose } = createAppStore(new FakeApplicationClient());
  return {
    stores: { workspace, panels, focus, chatInput, paneUi, app },
    dispose,
  };
}

function railsSnapshot(panels: readonly string[]): WorkspaceSnapshot {
  return {
    panelsVisible: panels as WorkspaceSnapshot['panelsVisible'],
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

describe('switchRepositoryWorkspace', () => {
  let stores: WorkspaceStores;
  let dispose: () => void;
  let storage: ReturnType<typeof memoryStorage>;

  beforeEach(() => {
    ({ stores, dispose } = makeStores());
    storage = memoryStorage();
    return () => dispose();
  });

  it('A→B→A restores layout, draft, and per-repo counts', () => {
    switchRepositoryWorkspace(stores, 'repo-main', { storage });
    applyWorkspaceCount(stores, 3, { storage });
    stores.panels.getState().show('plans');
    stores.chatInput.getState().insert('main draft');
    stores.paneUi.getState().setCursor('plans', 4);

    switchRepositoryWorkspace(stores, 'repo-side', { storage });
    expect(stores.workspace.getState().repositoryId).toBe('repo-side');
    expect(stores.workspace.getState().count).toBe(1);
    expect([...stores.panels.getState().visible]).toEqual([]);
    expect(stores.chatInput.getState().text).toBe('');

    applyWorkspaceCount(stores, 1, { storage });
    stores.chatInput.getState().insert('side draft');

    switchRepositoryWorkspace(stores, 'repo-main', { storage });
    expect(stores.workspace.getState().count).toBe(3);
    expect([...stores.panels.getState().visible]).toEqual(['plans']);
    expect(stores.chatInput.getState().text).toBe('main draft');
    expect(stores.paneUi.getState().cursors['plans']).toBe(4);
    expect(storage.getItem(workspaceCountStorageKey('repo-main'))).toBe('3');
    expect(storage.getItem(workspaceCountStorageKey('repo-side'))).toBe('1');
  });

  it('park + resume across app-store remount restores the bag into the new app store', () => {
    switchRepositoryWorkspace(stores, 'repo-a', { storage });
    applyWorkspaceCount(stores, 2, { storage });
    stores.chatInput.getState().insert('before remount');
    parkRepositoryWorkspace(stores, 'repo-a', { storage });
    expect(stores.workspace.getState().repositoryId).toBeNull();

    // Simulate session remount: new app store, same input stores.
    dispose();
    const next = createAppStore(new FakeApplicationClient());
    const remounted: WorkspaceStores = { ...stores, app: next.store };
    resumeRepositoryWorkspace(remounted, 'repo-a', { storage });
    expect(remounted.workspace.getState().repositoryId).toBe('repo-a');
    expect(remounted.workspace.getState().count).toBe(2);
    expect(remounted.chatInput.getState().text).toBe('before remount');
    next.dispose();
  });

  it('cold resume seeds count from storage when no parked bag exists', () => {
    storage.setItem(workspaceCountStorageKey('fresh'), '4');
    resumeRepositoryWorkspace(stores, 'fresh', { storage });
    expect(stores.workspace.getState().count).toBe(4);
    expect(stores.workspace.getState().slots).toHaveLength(4);
    expect(stores.workspace.getState().activeIndex).toBe(0);
  });

  it('cold resume hydrates freshSnapshot instead of wiping host defaults', () => {
    const fresh = railsSnapshot(['plans', 'notes', 'crows']);
    resumeRepositoryWorkspace(stores, 'web-fresh', {
      storage,
      freshSnapshot: fresh,
    });
    expect([...stores.panels.getState().visible].sort()).toEqual(['crows', 'notes', 'plans']);
    expect(stores.chatInput.getState().text).toBe('');
  });

  it('cold resume uses fallbackCount when storage is empty', () => {
    resumeRepositoryWorkspace(stores, 'tui-cold', { storage: null, fallbackCount: 3 });
    expect(stores.workspace.getState().count).toBe(3);
    expect(stores.workspace.getState().slots).toHaveLength(3);
  });

  it('park is a no-op when already unbound (StrictMode double-cleanup)', () => {
    switchRepositoryWorkspace(stores, 'repo-a', { storage });
    applyWorkspaceCount(stores, 2, { storage });
    stores.chatInput.getState().insert('keep');
    parkRepositoryWorkspace(stores, 'repo-a', { storage });
    const bag = stores.workspace.getState().getRepoBag('repo-a');
    stores.chatInput.getState().clear();
    stores.chatInput.getState().insert('mutated after park');
    parkRepositoryWorkspace(stores, 'repo-a', { storage });
    expect(stores.workspace.getState().getRepoBag('repo-a')).toEqual(bag);
    expect(bag?.slots[0]?.snapshot?.chatInput.buffer.text).toBe('keep');
  });

  it('switching to the already-bound repo is a no-op', () => {
    switchRepositoryWorkspace(stores, 'repo-a', { storage });
    stores.chatInput.getState().insert('keep');
    switchRepositoryWorkspace(stores, 'repo-a', { storage });
    expect(stores.chatInput.getState().text).toBe('keep');
    expect(stores.workspace.getState().getRepoBag('repo-a')).toBeNull();
  });
});
