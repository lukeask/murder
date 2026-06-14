/**
 * `chatHistoryStore` tests — the murder-wide sent-message recall ring.
 *
 * Covers:
 *  - starts empty;
 *  - `record` appends oldest→newest;
 *  - `record` ignores the empty string;
 *  - `record` collapses *consecutive* duplicates but keeps non-consecutive ones;
 *  - `seed` replaces the whole ring wholesale and copies (no aliasing the caller's array);
 *  - each write swaps the `entries` reference (so a subscriber re-renders).
 */

import { describe, expect, it } from 'vitest';
import { createChatHistoryStore } from '../../src/input/chatHistoryStore.js';

describe('chatHistoryStore', () => {
  it('starts with an empty ring', () => {
    const store = createChatHistoryStore();
    expect(store.getState().entries).toEqual([]);
  });

  it('record appends messages oldest→newest', () => {
    const store = createChatHistoryStore();
    store.getState().record('first');
    store.getState().record('second');
    expect(store.getState().entries).toEqual(['first', 'second']);
  });

  it('record ignores the empty string', () => {
    const store = createChatHistoryStore();
    store.getState().record('');
    expect(store.getState().entries).toEqual([]);
    store.getState().record('a');
    store.getState().record('');
    expect(store.getState().entries).toEqual(['a']);
  });

  it('record collapses a consecutive duplicate of the last entry', () => {
    const store = createChatHistoryStore();
    store.getState().record('same');
    store.getState().record('same');
    expect(store.getState().entries).toEqual(['same']);
  });

  it('record keeps a non-consecutive duplicate (revisiting an old message)', () => {
    const store = createChatHistoryStore();
    store.getState().record('a');
    store.getState().record('b');
    store.getState().record('a');
    expect(store.getState().entries).toEqual(['a', 'b', 'a']);
  });

  it('seed replaces the whole ring wholesale', () => {
    const store = createChatHistoryStore();
    store.getState().record('stale');
    store.getState().seed(['x', 'y', 'z']);
    expect(store.getState().entries).toEqual(['x', 'y', 'z']);
  });

  it('seed copies the caller array — later mutation does not alias state', () => {
    const store = createChatHistoryStore();
    const src = ['one', 'two'];
    store.getState().seed(src);
    src.push('three');
    expect(store.getState().entries).toEqual(['one', 'two']);
  });

  it('seed then record continues appending after the seeded entries', () => {
    const store = createChatHistoryStore();
    store.getState().seed(['old1', 'old2']);
    store.getState().record('new');
    expect(store.getState().entries).toEqual(['old1', 'old2', 'new']);
  });

  it('a no-op record (empty / consecutive dup) does not swap the entries reference', () => {
    const store = createChatHistoryStore();
    store.getState().record('a');
    const before = store.getState().entries;
    store.getState().record('');
    expect(store.getState().entries).toBe(before);
    store.getState().record('a'); // consecutive dup
    expect(store.getState().entries).toBe(before);
  });

  it('an effective record swaps the entries reference (subscriber re-render)', () => {
    const store = createChatHistoryStore();
    const before = store.getState().entries;
    store.getState().record('a');
    expect(store.getState().entries).not.toBe(before);
  });
});
