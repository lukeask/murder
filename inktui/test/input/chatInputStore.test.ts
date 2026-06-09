/**
 * `chatInputStore` (span-aware) tests — the F9 marked-image-span buffer logic.
 *
 * Covers:
 *  - inserting a marked span (the buffer holds the *id*, wrapped in PUA delimiters, never the number);
 *  - span-aware backspace at the trailing edge removes the WHOLE span and returns its id (so the
 *    handler can drop the imageDraftStore entry), while a trailing plain char is deleted one-at-a-time;
 *  - render derivation of `[Image N]` by positional count (delete renumbers for free);
 *  - submit expansion: done spans → `![image]({path})`, failed/absent spans → stripped.
 */

import { describe, expect, it } from 'vitest';
import { displayBuffer } from '../../src/components/ChatInput.js';
import {
  createChatInputStore,
  expandSpans,
  makeSpan,
  SPAN_CLOSE,
  SPAN_OPEN,
  spanIds,
  spanLabels,
} from '../../src/input/chatInputStore.js';

describe('chatInputStore — marked image spans', () => {
  it('appendImageSpan wraps the id in PUA delimiters, holding the id not the number', () => {
    const store = createChatInputStore();
    store.getState().append('hi ');
    store.getState().appendImageSpan('img-1');
    expect(store.getState().text).toBe(`hi ${SPAN_OPEN}img-1${SPAN_CLOSE}`);
    // The visible buffer never contains the literal "[Image 1]".
    expect(store.getState().text).not.toContain('[Image');
  });

  it('backspace at a trailing span removes the WHOLE span and returns its id', () => {
    const store = createChatInputStore();
    store.getState().append('a');
    store.getState().appendImageSpan('img-9');
    const removed = store.getState().backspace();
    expect(removed).toBe('img-9');
    expect(store.getState().text).toBe('a');
  });

  it('backspace on a trailing plain char deletes one char and returns null', () => {
    const store = createChatInputStore();
    store.getState().append('ab');
    const removed = store.getState().backspace();
    expect(removed).toBeNull();
    expect(store.getState().text).toBe('a');
  });

  it('backspace on an empty buffer is a no-op returning null', () => {
    const store = createChatInputStore();
    expect(store.getState().backspace()).toBeNull();
    expect(store.getState().text).toBe('');
  });

  it('appended chars never split a trailing span (span stays atomic)', () => {
    const store = createChatInputStore();
    store.getState().appendImageSpan('img-1');
    store.getState().append('!');
    // The char lands AFTER the closed span; the span is intact.
    expect(store.getState().text).toBe(`${makeSpan('img-1')}!`);
  });
});

describe('spanLabels / displayBuffer — derived [Image N]', () => {
  it('numbers spans positionally so deletion renumbers for free', () => {
    const text = `${makeSpan('a')}x${makeSpan('b')}y${makeSpan('c')}`;
    expect(spanLabels(text).map((s) => s.label)).toEqual(['[Image 1]', '[Image 2]', '[Image 3]']);
    expect(displayBuffer(text)).toBe('[Image 1]x[Image 2]y[Image 3]');
    // Drop the middle span → the third becomes [Image 2].
    const after = `${makeSpan('a')}x y${makeSpan('c')}`;
    expect(displayBuffer(after)).toBe('[Image 1]x y[Image 2]');
  });
});

describe('expandSpans — submit-time markdown expansion', () => {
  it('expands done spans to ![image](path) and strips absent (failed) ones', () => {
    const text = `look ${makeSpan('ok')} and ${makeSpan('bad')}`;
    const paths = new Map([['ok', '/img/ok.png']]); // 'bad' absent → failed/stripped
    expect(expandSpans(text, paths)).toBe('look ![image](/img/ok.png) and ');
  });

  it('passes plain text through untouched', () => {
    expect(expandSpans('hello world', new Map())).toBe('hello world');
  });

  it('spanIds lists every span id in order', () => {
    expect(spanIds(`${makeSpan('a')}x${makeSpan('b')}`)).toEqual(['a', 'b']);
  });
});
