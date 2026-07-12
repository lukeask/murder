import { describe, expect, it } from 'vitest';
import { terminalSafeText } from '../../src/utils/terminalSafeText.js';

describe('terminalSafeText — cookbook', () => {
  it('normalizes CR and expands tabs', () => {
    expect(terminalSafeText('a\r\nb\tc')).toBe('a\nb    c');
  });

  it('strips backspace and other C0 controls while keeping newlines', () => {
    expect(terminalSafeText('ab\b\nc')).toBe('ab\nc');
  });

  it('strips CSI cursor movement and SGR', () => {
    expect(terminalSafeText('hi\u001B[2Athere\u001B[31mred\u001B[0m')).toBe('hitherered');
  });

  it('strips OSC sequences terminated by BEL or ST', () => {
    expect(terminalSafeText('x\u001B]0;title\u0007y')).toBe('xy');
    expect(terminalSafeText('x\u001B]0;title\u001B\\y')).toBe('xy');
  });
});

describe('terminalSafeText — edge', () => {
  it('can leave ANSI when stripAnsi is false, still dropping C0', () => {
    expect(terminalSafeText('a\u001B[31mb\bc', { stripAnsi: false })).toBe('a\u001B[31mbc');
  });
});
