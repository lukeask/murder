/**
 * Workspace switch + count-clamp tests for the web composer bundle.
 * Exercises ui-core `switchWorkspace` / `applyWorkspaceCount` through web helpers.
 */

import { describe, expect, it, afterEach } from 'vitest';
import { createAppStore } from '@murder/ui-core/store/store.js';
import { FakeApplicationClient } from '@murder/ui-core/application/FakeApplicationClient.js';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import {
  createComposerStores,
  toWorkspaceStores,
} from '../src/composer/createComposerStores.js';
import {
  applyWorkspaceCount,
  workspaceJump,
  workspaceNext,
  workspacePrev,
} from '../src/composer/workspaceActions.js';
import { WorkspaceStrip } from '../src/components/WorkspaceStrip.js';
import { panelFocusStore } from '../src/panelFocus.js';
import { renderWithStore } from './helpers.js';

afterEach(() => {
  cleanup();
  panelFocusStore.getState().clear();
});

function makeBundle() {
  const bus = new FakeApplicationClient();
  const { store: app } = createAppStore(bus);
  const composer = createComposerStores();
  const stores = toWorkspaceStores(composer, app);
  return { app, composer, stores };
}

describe('workspace switch (web helpers)', () => {
  it('cycles next/prev and restores chat draft per workspace', () => {
    const { composer, stores } = makeBundle();
    applyWorkspaceCount(stores, 3);

    composer.chatInput.getState().insert('draft-ws0');
    workspaceNext(stores);
    expect(composer.workspace.getState().activeIndex).toBe(1);
    expect(composer.chatInput.getState().text).toBe('');

    composer.chatInput.getState().insert('draft-ws1');
    workspacePrev(stores);
    expect(composer.workspace.getState().activeIndex).toBe(0);
    expect(composer.chatInput.getState().text).toBe('draft-ws0');

    workspaceJump(stores, 1);
    expect(composer.workspace.getState().activeIndex).toBe(1);
    expect(composer.chatInput.getState().text).toBe('draft-ws1');
  });

  it('jump past count is a no-op', () => {
    const { composer, stores } = makeBundle();
    applyWorkspaceCount(stores, 2);
    workspaceJump(stores, 5);
    expect(composer.workspace.getState().activeIndex).toBe(0);
  });

  it('clamps active index when count shrinks and hydrates surviving slot', () => {
    const { composer, stores } = makeBundle();
    applyWorkspaceCount(stores, 3);
    composer.chatInput.getState().insert('on-ws0');
    workspaceJump(stores, 2);
    composer.chatInput.getState().insert('on-ws2');

    applyWorkspaceCount(stores, 2);
    expect(composer.workspace.getState().count).toBe(2);
    expect(composer.workspace.getState().activeIndex).toBe(1);
    // Slot 1 was never opened → fresh-boot empty draft after clamp hydrate.
    expect(composer.chatInput.getState().text).toBe('');
  });

  it('restores pane target across switch', () => {
    const { app, composer, stores } = makeBundle();
    applyWorkspaceCount(stores, 2);
    app.getState().actions.conversations.setActivePaneAgentId('agent-a');
    workspaceNext(stores);
    expect(app.getState().conversations.activePaneAgentId).toBe(null);
    workspacePrev(stores);
    expect(app.getState().conversations.activePaneAgentId).toBe('agent-a');
    expect(composer.workspace.getState().activeIndex).toBe(0);
  });

  it('restores list cursor across A→B→A', () => {
    const { composer, stores } = makeBundle();
    applyWorkspaceCount(stores, 2);

    composer.paneUi.getState().setCursor('crows', 4);
    composer.paneUi.getState().setExpanded('crows', false);
    workspaceNext(stores);
    expect(composer.paneUi.getState().cursors['crows'] ?? 0).toBe(0);
    expect(composer.paneUi.getState().expandeds['crows'] ?? false).toBe(false);

    composer.paneUi.getState().setCursor('crows', 1);
    workspacePrev(stores);
    expect(composer.workspace.getState().activeIndex).toBe(0);
    expect(composer.paneUi.getState().cursors['crows']).toBe(4);
    expect(composer.paneUi.getState().expandeds['crows']).toBe(false);
  });

  it('restores panelFocus from snapshotted focus intended id across A→B→A', () => {
    const { composer, stores } = makeBundle();
    applyWorkspaceCount(stores, 2);

    panelFocusStore.getState().focus('history');
    workspaceNext(stores);
    // Fresh slot → chat → rail focus cleared.
    expect(panelFocusStore.getState().focusedId).toBeNull();
    expect(composer.focus.getState().intendedId).toBe('chat');

    panelFocusStore.getState().focus('usage');
    workspacePrev(stores);
    expect(composer.workspace.getState().activeIndex).toBe(0);
    expect(panelFocusStore.getState().focusedId).toBe('history');
    expect(composer.focus.getState().intendedId).toBe('history');
  });

  it('restores doc/transcript scroll offsets across A→B→A', () => {
    const { composer, stores } = makeBundle();
    applyWorkspaceCount(stores, 2);

    composer.paneUi.getState().setScroll('stage:doc:plan-a', 120);
    composer.paneUi.getState().setScroll('stage:transcript:agent-1', 48);
    workspaceNext(stores);
    expect(composer.paneUi.getState().scrolls['stage:doc:plan-a'] ?? 0).toBe(0);
    expect(composer.paneUi.getState().scrolls['stage:transcript:agent-1'] ?? 0).toBe(0);

    workspacePrev(stores);
    expect(composer.paneUi.getState().scrolls['stage:doc:plan-a']).toBe(120);
    expect(composer.paneUi.getState().scrolls['stage:transcript:agent-1']).toBe(48);
  });
});

describe('WorkspaceStrip', () => {
  it('renders digits when count > 1 and jumps on click', () => {
    const composer = createComposerStores();
    const bus = new FakeApplicationClient();
    const { store } = createAppStore(bus);
    applyWorkspaceCount(toWorkspaceStores(composer, store), 3);

    renderWithStore(<WorkspaceStrip />, { composer, store, bus });

    expect(screen.getByRole('tablist', { name: 'workspaces' })).toBeTruthy();
    fireEvent.click(screen.getByRole('tab', { name: 'workspace 2' }));
    expect(composer.workspace.getState().activeIndex).toBe(1);
  });

  it('hides when count is 1', () => {
    const composer = createComposerStores();
    renderWithStore(<WorkspaceStrip />, { composer });
    expect(screen.queryByRole('tablist', { name: 'workspaces' })).toBeNull();
  });
});
