/**
 * Workspace switch + count-clamp tests for the web composer bundle.
 * Exercises ui-core `switchWorkspace` / `applyWorkspaceCount` through web helpers.
 */

import { describe, expect, it } from 'vitest';
import { applyWorkspaceCount } from '@murder/ui-core/input/workspaceSwitch.js';
import { createAppStore } from '@murder/ui-core/store/store.js';
import { FakeApplicationClient } from '@murder/ui-core/application/FakeApplicationClient.js';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import { afterEach } from 'vitest';
import {
  createComposerStores,
  toWorkspaceStores,
} from '../src/composer/createComposerStores.js';
import {
  workspaceJump,
  workspaceNext,
  workspacePrev,
} from '../src/composer/workspaceActions.js';
import { WorkspaceStrip } from '../src/components/WorkspaceStrip.js';
import { renderWithStore } from './helpers.js';

afterEach(cleanup);

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
