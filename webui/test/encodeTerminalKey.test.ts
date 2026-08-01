/**
 * encodeTerminalKey: browser KeyboardEvent → VT/xterm stdin bytes.
 */

import { describe, expect, it } from 'vitest';
import { encodeTerminalKey } from '../src/components/stage/encodeTerminalKey.js';

function keyEvent(init: KeyboardEventInit & { key: string }): KeyboardEvent {
  return new KeyboardEvent('keydown', init);
}

function decode(bytes: Uint8Array | null): string | null {
  if (bytes === null) return null;
  return new TextDecoder().decode(bytes);
}

describe('encodeTerminalKey', () => {
  it('encodes printable, enter, tab, backspace, and escape', () => {
    expect(decode(encodeTerminalKey(keyEvent({ key: 'a' })))).toBe('a');
    expect(decode(encodeTerminalKey(keyEvent({ key: 'é' })))).toBe('é');
    expect(Array.from(encodeTerminalKey(keyEvent({ key: 'Enter' }))!)).toEqual([0x0d]);
    expect(Array.from(encodeTerminalKey(keyEvent({ key: 'Tab' }))!)).toEqual([0x09]);
    expect(decode(encodeTerminalKey(keyEvent({ key: 'Tab', shiftKey: true })))).toBe('\x1b[Z');
    expect(Array.from(encodeTerminalKey(keyEvent({ key: 'Backspace' }))!)).toEqual([0x7f]);
    expect(Array.from(encodeTerminalKey(keyEvent({ key: 'Escape' }))!)).toEqual([0x1b]);
  });

  it('encodes arrows and modified arrows as CSI', () => {
    expect(decode(encodeTerminalKey(keyEvent({ key: 'ArrowUp' })))).toBe('\x1b[A');
    expect(decode(encodeTerminalKey(keyEvent({ key: 'ArrowLeft', ctrlKey: true })))).toBe(
      '\x1b[1;5D',
    );
  });

  it('encodes Ctrl letters and Alt printable', () => {
    expect(Array.from(encodeTerminalKey(keyEvent({ key: 'c', ctrlKey: true }))!)).toEqual([0x03]);
    expect(decode(encodeTerminalKey(keyEvent({ key: 'x', altKey: true })))).toBe('\x1bx');
  });

  it('leaves Meta chords and lone modifiers for the browser', () => {
    expect(encodeTerminalKey(keyEvent({ key: 'r', metaKey: true }))).toBeNull();
    expect(encodeTerminalKey(keyEvent({ key: 'Shift' }))).toBeNull();
  });
});
