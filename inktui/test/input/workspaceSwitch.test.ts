/**
 * `switchWorkspace` pipeline tests — serialize/hydrate round-trips over the real stores
 * (workspaces plan, step 2a).
 *
 * Cookbook first: switch 1→2→1 restores layout, focus, pane UI state, and the chat draft; a
 * never-opened slot hydrates the chat-only fresh-boot layout. Then edge cases: jump past count,
 * switch to self, transition-in-flight guard, shrink-while-active clamping, JSON-cleanliness.
 */

import { beforeEach, describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import {
  applyWorkspaceCount,
  serializeWorkspaceSnapshot,
  switchWorkspace,
  type WorkspaceStores,
} from '../../src/input/workspaceSwitch.js';
import { createAppStore } from '../../src/store/store.js';

const plansPaneId = 'plans';
const crowsPaneId = 'crows';

/** Build the full pipeline store set: the real input-store bundle + a fake-bus app store. */
function makeStores(count = 3): { stores: WorkspaceStores; dispose: () => void } {
  const input = createInputStores();
  const { store: app, dispose } = createAppStore(new FakeBusClient());
  input.workspace.getState().setCount(count);
  const stores: WorkspaceStores = {
    workspace: input.workspace,
    panels: input.panels,
    focus: input.focus,
    chatInput: input.chatInput,
    paneUi: input.paneUi,
    app,
  };
  return { stores, dispose };
}

/** Dress the live stores as a distinctive "workspace 1" layout the tests can recognise. */
function arrangeWorkspaceOne(stores: WorkspaceStores): void {
  stores.panels.getState().show('plans');
  stores.panels.getState().show('crows');
  stores.focus.getState().focus('plans');
  stores.paneUi.getState().setCursor('plans', 3);
  stores.paneUi.getState().setScroll('stage:transcript:crow-1', 12);
  stores.paneUi.getState().setExpanded('crows', true);
  stores.chatInput.getState().insert('draft for workspace one');
  stores.app.setState((state) => ({
    conversations: {
      ...state.conversations,
      activePaneAgentId: 'crow-1',
      paneOverrides: new Map([['crow-1', true]]),
      paneViewModes: { 'crow-1': 'condensed' as const },
    },
  }));
}

describe('switchWorkspace (cookbook)', () => {
  let stores: WorkspaceStores;
  let dispose: () => void;

  beforeEach(() => {
    ({ stores, dispose } = makeStores());
    return () => dispose();
  });

  it('switching to a never-opened workspace lands on the chat-only fresh-boot layout', () => {
    arrangeWorkspaceOne(stores);
    switchWorkspace(stores, 1, 'next');

    expect(stores.workspace.getState().activeIndex).toBe(1);
    expect([...stores.panels.getState().visible]).toEqual([]);
    expect(stores.focus.getState().intendedId).toBe('chat');
    expect(stores.chatInput.getState().text).toBe('');
    expect(stores.paneUi.getState().cursors).toEqual({});
    expect(stores.paneUi.getState().scrolls).toEqual({});
    expect(stores.app.getState().conversations.activePaneAgentId).toBeNull();
    expect(stores.app.getState().conversations.paneOverrides.size).toBe(0);
  });

  it('switching 1→2→1 restores layout, focus, pane UI state, and the chat draft', () => {
    arrangeWorkspaceOne(stores);
    switchWorkspace(stores, 1, 'next');

    // Make workspace 2 look different, so a restore can't pass by accident.
    stores.panels.getState().show('tickets');
    stores.chatInput.getState().insert('workspace two draft');
    stores.paneUi.getState().setCursor('tickets', 7);

    switchWorkspace(stores, 0, 'prev');

    expect(stores.workspace.getState().activeIndex).toBe(0);
    expect([...stores.panels.getState().visible].sort()).toEqual(['crows', 'plans']);
    expect(stores.focus.getState().intendedId).toBe('plans');
    expect(stores.chatInput.getState().text).toBe('draft for workspace one');
    expect(stores.chatInput.getState().cursor).toBe('draft for workspace one'.length);
    expect(stores.paneUi.getState().cursors[plansPaneId]).toBe(3);
    expect(stores.paneUi.getState().scrolls['stage:transcript:crow-1']).toBe(12);
    expect(stores.paneUi.getState().expandeds[crowsPaneId]).toBe(true);
    expect(stores.app.getState().conversations.activePaneAgentId).toBe('crow-1');
    expect(stores.app.getState().conversations.paneOverrides.get('crow-1')).toBe(true);
    expect(stores.app.getState().conversations.paneViewModes['crow-1']).toBe('condensed');
  });

  it('restores chat history-nav state (recalled entry + stashed draft) verbatim', () => {
    stores.chatInput.getState().insert('live draft');
    stores.chatInput.getState().historyPrev(['older entry']);
    expect(stores.chatInput.getState().historyIndex).toBe(0);

    switchWorkspace(stores, 1, 'next');
    expect(stores.chatInput.getState().historyIndex).toBeNull();

    switchWorkspace(stores, 0, 'prev');
    expect(stores.chatInput.getState().text).toBe('older entry');
    expect(stores.chatInput.getState().historyIndex).toBe(0);
    expect(stores.chatInput.getState().stashedDraft).toEqual({
      text: 'live draft',
      cursor: 'live draft'.length,
    });
  });

  it('restores an open doc pane by re-fetching it through the docView action', () => {
    // Open a doc in workspace 1 (fake bus has no stub → status will settle to error, which is
    // fine: the snapshot carries identity, not the body).
    void stores.app.getState().actions.docView.open('plan', 'workspaces');
    expect(stores.app.getState().docView.open).toEqual({ kind: 'plan', name: 'workspaces' });

    switchWorkspace(stores, 1, 'next');
    expect(stores.app.getState().docView.open).toBeNull();

    switchWorkspace(stores, 0, 'prev');
    expect(stores.app.getState().docView.open).toEqual({ kind: 'plan', name: 'workspaces' });
  });

  it('invokes the injected frame capture and repaint hooks', () => {
    const frame = { text: 'f', columns: 80, rows: 24 };
    let repaints = 0;
    switchWorkspace(stores, 1, 'next', { captureFrame: () => frame, repaint: () => repaints++ });
    expect(stores.workspace.getState().slots[0]?.lastFrame).toBe(frame);
    expect(repaints).toBe(1);
  });

  it('begins a slide when the target slot has a same-size cached frame (switch still commits)', () => {
    const fromFrame = { text: 'from', columns: 80, rows: 24 };
    const toFrame = { text: 'to', columns: 80, rows: 24 };
    stores.workspace.getState().saveSlot(1, null, toFrame);
    let repaints = 0;
    switchWorkspace(stores, 1, 'next', {
      captureFrame: () => fromFrame,
      repaint: () => repaints++,
    });

    // The switch is committed immediately — the transition is cosmetic paint state on top.
    expect(stores.workspace.getState().activeIndex).toBe(1);
    const transition = stores.workspace.getState().transition;
    expect(transition).not.toBeNull();
    expect(transition?.fromFrame).toBe(fromFrame);
    expect(transition?.toFrame).toBe(toFrame);
    expect(transition?.direction).toBe('next');
    // The end-of-slide repaint belongs to the overlay, not the pipeline.
    expect(repaints).toBe(0);
  });
});

describe('switchWorkspace (edge cases)', () => {
  let stores: WorkspaceStores;
  let dispose: () => void;

  beforeEach(() => {
    ({ stores, dispose } = makeStores());
    return () => dispose();
  });

  it('jump past count is a no-op', () => {
    arrangeWorkspaceOne(stores);
    switchWorkspace(stores, 5, 'next');
    expect(stores.workspace.getState().activeIndex).toBe(0);
    // Live state untouched — not even serialized into the slot.
    expect(stores.workspace.getState().slots[0]?.snapshot).toBeNull();
    expect(stores.chatInput.getState().text).toBe('draft for workspace one');
  });

  it('switching to the active workspace is a no-op', () => {
    arrangeWorkspaceOne(stores);
    switchWorkspace(stores, 0, 'next');
    expect(stores.workspace.getState().slots[0]?.snapshot).toBeNull();
    expect(stores.chatInput.getState().text).toBe('draft for workspace one');
  });

  it('ignores switches while a transition is in flight', () => {
    const frame = { text: 'f', columns: 80, rows: 24 };
    stores.workspace
      .getState()
      .beginTransition({ fromFrame: frame, toFrame: frame, direction: 'next', startedAt: 0 });
    switchWorkspace(stores, 1, 'next');
    expect(stores.workspace.getState().activeIndex).toBe(0);
  });

  it('skips the slide when the cached target frame was captured at a different size', () => {
    // Cached before a "resize": stale geometry ⇒ instant switch, no transition.
    stores.workspace.getState().saveSlot(1, null, { text: 'old', columns: 120, rows: 40 });
    let repaints = 0;
    switchWorkspace(stores, 1, 'next', {
      captureFrame: () => ({ text: 'now', columns: 80, rows: 24 }),
      repaint: () => repaints++,
    });
    expect(stores.workspace.getState().activeIndex).toBe(1);
    expect(stores.workspace.getState().transition).toBeNull();
    expect(repaints).toBe(1);
  });

  it('skips the slide when the source capture returns null (Ink internals unavailable)', () => {
    stores.workspace.getState().saveSlot(1, null, { text: 'to', columns: 80, rows: 24 });
    switchWorkspace(stores, 1, 'next', { captureFrame: () => null });
    expect(stores.workspace.getState().activeIndex).toBe(1);
    expect(stores.workspace.getState().transition).toBeNull();
  });

  it('at count 1 the feature is inert (every target is out of range or self)', () => {
    const single = makeStores(1);
    arrangeWorkspaceOne(single.stores);
    switchWorkspace(single.stores, 1, 'next');
    switchWorkspace(single.stores, 0, 'next');
    expect(single.stores.workspace.getState().activeIndex).toBe(0);
    expect(single.stores.chatInput.getState().text).toBe('draft for workspace one');
    single.dispose();
  });

  it('shrink-while-active clamps to the last workspace and hydrates its snapshot', () => {
    // Workspace 0 gets the distinctive layout; workspace 2 is where we sit when the shrink lands.
    arrangeWorkspaceOne(stores);
    switchWorkspace(stores, 1, 'next');
    stores.chatInput.getState().insert('second');
    switchWorkspace(stores, 2, 'next');
    stores.chatInput.getState().insert('doomed workspace draft');

    let repaints = 0;
    applyWorkspaceCount(stores, 2, { repaint: () => repaints++ });

    expect(stores.workspace.getState().count).toBe(2);
    expect(stores.workspace.getState().slots).toHaveLength(2);
    expect(stores.workspace.getState().activeIndex).toBe(1);
    // Landed on workspace 2's (index 1) saved state; the dropped live draft is gone.
    expect(stores.chatInput.getState().text).toBe('second');
    expect(repaints).toBe(1);
  });

  it('shrink that keeps the active workspace does not touch the live stores', () => {
    switchWorkspace(stores, 1, 'next');
    stores.chatInput.getState().insert('still here');
    applyWorkspaceCount(stores, 2);
    expect(stores.workspace.getState().activeIndex).toBe(1);
    expect(stores.chatInput.getState().text).toBe('still here');
  });

  it('growing the count adds never-opened slots and keeps the active workspace', () => {
    arrangeWorkspaceOne(stores);
    applyWorkspaceCount(stores, 5);
    expect(stores.workspace.getState().count).toBe(5);
    expect(stores.workspace.getState().activeIndex).toBe(0);
    expect(stores.chatInput.getState().text).toBe('draft for workspace one');
    expect(stores.workspace.getState().slots[4]).toEqual({ snapshot: null, lastFrame: null });
  });

  it('snapshots are plain JSON data (persisting slots later must be cheap)', () => {
    arrangeWorkspaceOne(stores);
    stores.app.setState((state) => ({
      conversations: {
        ...state.conversations,
        paneReapAges: new Map([['stage:transcript:crow-1', 2]]),
      },
    }));
    void stores.app.getState().actions.docView.open('note', 'scratch');
    const snapshot = serializeWorkspaceSnapshot(stores);
    expect(JSON.parse(JSON.stringify(snapshot))).toEqual(snapshot);
  });

  it('hydrating a snapshot is decoupled from later live mutations (no shared references)', () => {
    arrangeWorkspaceOne(stores);
    switchWorkspace(stores, 1, 'next');
    switchWorkspace(stores, 0, 'prev');
    const saved = stores.workspace.getState().slots[1]?.snapshot ?? null;
    // Mutating the live workspace after the round-trip must not bleed into workspace 2's slot.
    stores.paneUi.getState().setCursor('plans', 99);
    expect(saved?.paneUi.cursors ?? {}).toEqual({});
  });
});
