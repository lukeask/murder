/**
 * `paneUiStore` tests — the per-pane ephemeral UI state (scroll/cursor keyed by pane id).
 *
 * Covers:
 *  - starts empty (no remembered positions);
 *  - a missing id reads as `0` at the selector level (caller default);
 *  - `setCursor` / `setScroll` write per id and are independent;
 *  - two panes' values don't collide;
 *  - each write swaps the containing map's reference (so a subscriber re-renders) and leaves the
 *    other map's reference untouched.
 */

import { describe, expect, it } from 'vitest';
import { createPaneUiStore, DEFAULT_TRANSIT_CURSOR } from '../../src/input/paneUiStore.js';

describe('paneUiStore', () => {
  const plans = 'plans';
  const history = 'history';
  const tree = 'tree';

  it('starts with no remembered positions', () => {
    const store = createPaneUiStore();
    expect(store.getState().cursors).toEqual({});
    expect(store.getState().scrolls).toEqual({});
    expect(store.getState().expandeds).toEqual({});
    expect(store.getState().historyModes).toEqual({});
    expect(store.getState().transitCursors).toEqual({});
    expect(store.getState().gBuffers).toEqual({});
  });

  it('reads a missing id as undefined (callers default to 0)', () => {
    const store = createPaneUiStore();
    expect(store.getState().cursors[plans] ?? 0).toBe(0);
    expect(store.getState().scrolls[plans] ?? 0).toBe(0);
    expect(store.getState().expandeds[plans] ?? false).toBe(false);
    expect(store.getState().historyModes[history] ?? 'loose').toBe('loose');
    expect(store.getState().transitCursors[tree] ?? DEFAULT_TRANSIT_CURSOR).toEqual(
      DEFAULT_TRANSIT_CURSOR,
    );
    expect(store.getState().gBuffers[tree] ?? null).toBe(null);
  });

  it('setCursor stores the value under the pane id', () => {
    const store = createPaneUiStore();
    store.getState().setCursor('plans', 3);
    expect(store.getState().cursors[plans]).toBe(3);
  });

  it('setScroll stores the value under the pane id', () => {
    const store = createPaneUiStore();
    store.getState().setScroll('doc:scroll', 5);
    expect(store.getState().scrolls['doc:scroll']).toBe(5);
  });

  it('keeps distinct panes independent', () => {
    const store = createPaneUiStore();
    store.getState().setCursor('plans', 2);
    store.getState().setCursor('reports', 7);
    expect(store.getState().cursors).toEqual({ plans: 2, reports: 7 });
  });

  it('setExpanded stores the value under the pane id', () => {
    const store = createPaneUiStore();
    store.getState().setExpanded('crows', true);
    const crows = 'crows';
    expect(store.getState().expandeds[crows]).toBe(true);
  });

  it('setHistoryMode stores the value under the pane id', () => {
    const store = createPaneUiStore();
    store.getState().setHistoryMode('history', 'all');
    expect(store.getState().historyModes[history]).toBe('all');
  });

  it('cursors, scrolls, expandeds, and historyModes are separate namespaces', () => {
    const store = createPaneUiStore();
    store.getState().setCursor('plans', 1);
    store.getState().setScroll('plans', 9);
    store.getState().setExpanded('plans', true);
    store.getState().setHistoryMode('history', 'all');
    expect(store.getState().cursors[plans]).toBe(1);
    expect(store.getState().scrolls[plans]).toBe(9);
    expect(store.getState().expandeds[plans]).toBe(true);
    expect(store.getState().historyModes[history]).toBe('all');
  });

  it('stores raw (unclamped) values verbatim', () => {
    const store = createPaneUiStore();
    store.getState().setCursor('plans', 999);
    store.getState().setScroll('plans', -4);
    expect(store.getState().cursors[plans]).toBe(999);
    expect(store.getState().scrolls[plans]).toBe(-4);
  });

  it('a setCursor swaps the cursors reference but not scrolls (subscriber re-render)', () => {
    const store = createPaneUiStore();
    const cursorsBefore = store.getState().cursors;
    const scrollsBefore = store.getState().scrolls;
    store.getState().setCursor('plans', 1);
    expect(store.getState().cursors).not.toBe(cursorsBefore);
    expect(store.getState().scrolls).toBe(scrollsBefore);
  });

  it('a setScroll swaps the scrolls reference but not cursors', () => {
    const store = createPaneUiStore();
    const cursorsBefore = store.getState().cursors;
    const scrollsBefore = store.getState().scrolls;
    store.getState().setScroll('plans', 1);
    expect(store.getState().scrolls).not.toBe(scrollsBefore);
    expect(store.getState().cursors).toBe(cursorsBefore);
  });

  it('setTransitCursor stores the value under the pane id', () => {
    const store = createPaneUiStore();
    const cursor = { laneIndex: 2, sha: 'abc123' };
    store.getState().setTransitCursor('tree', cursor);
    expect(store.getState().transitCursors[tree]).toEqual(cursor);
  });

  it('setGBuffer stores the value under the pane id', () => {
    const store = createPaneUiStore();
    store.getState().setGBuffer('tree', '5d');
    expect(store.getState().gBuffers[tree]).toBe('5d');
    store.getState().setGBuffer('tree', null);
    expect(store.getState().gBuffers[tree]).toBe(null);
  });

  it('a setTransitCursor swaps the transitCursors reference but not cursors', () => {
    const store = createPaneUiStore();
    const cursorsBefore = store.getState().cursors;
    const transitCursorsBefore = store.getState().transitCursors;
    store.getState().setTransitCursor('tree', { laneIndex: 1, sha: 'deadbeef' });
    expect(store.getState().transitCursors).not.toBe(transitCursorsBefore);
    expect(store.getState().cursors).toBe(cursorsBefore);
  });

  it('a setExpanded swaps the expandeds reference but not cursors or scrolls', () => {
    const store = createPaneUiStore();
    const cursorsBefore = store.getState().cursors;
    const scrollsBefore = store.getState().scrolls;
    const expandedsBefore = store.getState().expandeds;
    store.getState().setExpanded('crows', true);
    expect(store.getState().expandeds).not.toBe(expandedsBefore);
    expect(store.getState().cursors).toBe(cursorsBefore);
    expect(store.getState().scrolls).toBe(scrollsBefore);
  });
});
