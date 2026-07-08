/**
 * `workspaceStore` tests — the slot array + active-index state (workspaces plan, step 2a).
 *
 * Cookbook first (boot shape, saveSlot, setActiveIndex, count grow/shrink), then edge cases
 * (shrink clamping the active index, out-of-range writes as no-ops).
 * The switch *pipeline* (serialize/hydrate round-trips) is covered in workspaceSwitch.test.ts.
 */

import { describe, expect, it } from 'vitest';
import {
  type CapturedFrame,
  createWorkspaceStore,
  type WorkspaceSnapshot,
} from '../../src/input/workspaceStore.js';

/** A minimal well-formed snapshot for slot-write assertions (content is opaque to the store). */
function fakeSnapshot(marker: string): WorkspaceSnapshot {
  return {
    panelsVisible: [],
    focusIntendedId: 'chat',
    paneUi: {
      cursors: {},
      scrolls: {},
      expandeds: {},
      historyModes: {},
      gotoLines: {},
      transitCursors: {},
      gBuffers: {},
    },
    chatInput: { buffer: { text: marker, cursor: 0 }, historyIndex: null, stashedDraft: null },
    conversations: {
      activePaneAgentId: null,
      paneOverrides: {},
      paneReapAges: {},
      paneViewModes: {},
    },
    docView: null,
  };
}

const FRAME: CapturedFrame = { text: 'frame', columns: 80, rows: 24 };

describe('workspaceStore', () => {
  it('boots inert: one workspace, index 0, one empty slot, no transition', () => {
    const store = createWorkspaceStore();
    expect(store.getState().count).toBe(1);
    expect(store.getState().activeIndex).toBe(0);
    expect(store.getState().slots).toEqual([{ snapshot: null, lastFrame: null }]);
    expect(store.getState().transition).toBeNull();
  });

  it('saveSlot writes a slot; the others keep identity', () => {
    const store = createWorkspaceStore(3);
    const before = store.getState().slots;
    const snapshot = fakeSnapshot('a');
    store.getState().saveSlot(1, snapshot, FRAME);
    const slots = store.getState().slots;
    expect(slots[1]).toEqual({ snapshot, lastFrame: FRAME });
    expect(slots[0]).toBe(before[0]);
    expect(slots[2]).toBe(before[2]);
  });

  it('setActiveIndex commits an in-range index', () => {
    const store = createWorkspaceStore(3);
    store.getState().setActiveIndex(2);
    expect(store.getState().activeIndex).toBe(2);
  });

  it('setCount grows with never-opened slots, keeping existing ones', () => {
    const store = createWorkspaceStore(2);
    const snapshot = fakeSnapshot('keep');
    store.getState().saveSlot(1, snapshot, null);
    store.getState().setCount(4);
    expect(store.getState().count).toBe(4);
    expect(store.getState().slots).toHaveLength(4);
    expect(store.getState().slots[1]?.snapshot).toBe(snapshot);
    expect(store.getState().slots[3]).toEqual({ snapshot: null, lastFrame: null });
  });

  it('setCount shrink drops slots above the new count', () => {
    const store = createWorkspaceStore(3);
    store.getState().saveSlot(2, fakeSnapshot('dropped'), FRAME);
    store.getState().setCount(2);
    expect(store.getState().slots).toHaveLength(2);
  });

  // ---- edge cases ----

  it('setCount shrink clamps the active index into range', () => {
    const store = createWorkspaceStore(3);
    store.getState().setActiveIndex(2);
    store.getState().setCount(2);
    expect(store.getState().activeIndex).toBe(1);
  });

  it('setCount clamps to a minimum of one workspace', () => {
    const store = createWorkspaceStore(3);
    store.getState().setCount(0);
    expect(store.getState().count).toBe(1);
    expect(store.getState().slots).toHaveLength(1);
  });

  it('setActiveIndex out of range is a no-op', () => {
    const store = createWorkspaceStore(2);
    store.getState().setActiveIndex(5);
    store.getState().setActiveIndex(-1);
    expect(store.getState().activeIndex).toBe(0);
  });

  it('saveSlot out of range is a no-op (keeps slots identity)', () => {
    const store = createWorkspaceStore(2);
    const before = store.getState().slots;
    store.getState().saveSlot(2, fakeSnapshot('x'), null);
    store.getState().saveSlot(-1, fakeSnapshot('y'), null);
    expect(store.getState().slots).toBe(before);
  });

  it('begin/clearTransition round-trips', () => {
    const store = createWorkspaceStore(2);
    const transition = {
      fromFrame: FRAME,
      toFrame: FRAME,
      direction: 'next' as const,
      startedAt: 1,
    };
    store.getState().beginTransition(transition);
    expect(store.getState().transition).toBe(transition);
    store.getState().clearTransition();
    expect(store.getState().transition).toBeNull();
  });
});
