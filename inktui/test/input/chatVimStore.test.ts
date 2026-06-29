/**
 * `chatVimStore` tests — vim submode + pending operator + the murder-wide yank register.
 *
 * Covers:
 *  - initial state (`normal`, no pending, empty register);
 *  - `setSubmode` flips normal↔insert;
 *  - `setPending` sets and clears (`null`) the pending operator;
 *  - `setRegister` replaces the register;
 *  - the register is murder-wide: a write through one handle is visible through the same single
 *    store instance (the cross-chat-target yank/paste invariant).
 */

import { describe, expect, it } from 'vitest';
import { createChatVimStore } from '../../src/input/chatVimStore.js';

describe('chatVimStore', () => {
  it('starts in normal submode, no pending, empty register', () => {
    const store = createChatVimStore();
    const s = store.getState();
    expect(s.submode).toBe('normal');
    expect(s.pending).toBeNull();
    expect(s.register).toBe('');
  });

  it('setSubmode flips normal↔insert', () => {
    const store = createChatVimStore();
    store.getState().setSubmode('insert');
    expect(store.getState().submode).toBe('insert');
    store.getState().setSubmode('normal');
    expect(store.getState().submode).toBe('normal');
  });

  it('setPending sets and clears the pending operator', () => {
    const store = createChatVimStore();
    store.getState().setPending('d');
    expect(store.getState().pending).toBe('d');
    store.getState().setPending(null);
    expect(store.getState().pending).toBeNull();
  });

  it('setRegister replaces the register text', () => {
    const store = createChatVimStore();
    store.getState().setRegister('yanked text');
    expect(store.getState().register).toBe('yanked text');
    store.getState().setRegister('newer');
    expect(store.getState().register).toBe('newer');
  });

  it('the register is shared across reads of the single store instance (murder-wide)', () => {
    // Two readers of the SAME store instance model two recipient targets sharing the one register.
    const store = createChatVimStore();
    const chatA = store; // crow A's view
    const chatB = store; // crow B's view
    chatA.getState().setRegister('from A');
    expect(chatB.getState().register).toBe('from A');
  });
});
