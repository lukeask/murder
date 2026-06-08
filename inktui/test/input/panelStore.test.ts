/**
 * panelStore tests — view state as a toggle set, ref-swapped on change.
 */

import { describe, expect, it } from 'vitest';
import { createPanelStore } from '../../src/input/panelStore.js';

describe('panelStore', () => {
  it('starts empty (no panels visible) by default', () => {
    const store = createPanelStore();
    expect([...store.getState().visible]).toEqual([]);
  });

  it('seeds an initial visible set', () => {
    const store = createPanelStore(['plans', 'crows']);
    expect(store.getState().visible.has('plans')).toBe(true);
    expect(store.getState().visible.has('crows')).toBe(true);
  });

  it('toggle turns a hidden panel on and a visible panel off', () => {
    const store = createPanelStore();
    store.getState().toggle('plans');
    expect(store.getState().visible.has('plans')).toBe(true);
    store.getState().toggle('plans');
    expect(store.getState().visible.has('plans')).toBe(false);
  });

  it('ref-swaps the set on change (so subscribers re-render)', () => {
    const store = createPanelStore();
    const before = store.getState().visible;
    store.getState().toggle('plans');
    expect(store.getState().visible).not.toBe(before);
  });

  it('show is idempotent and keeps identity when already visible', () => {
    const store = createPanelStore(['plans']);
    const before = store.getState().visible;
    store.getState().show('plans');
    expect(store.getState().visible).toBe(before); // unchanged → no spurious re-render
  });

  it('hide is idempotent and keeps identity when already hidden', () => {
    const store = createPanelStore();
    const before = store.getState().visible;
    store.getState().hide('plans');
    expect(store.getState().visible).toBe(before);
  });
});
