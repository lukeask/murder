/**
 * Terminal-size guard math — the pure clamp behind `useModalWidth` and the min-size floor's
 * relationship to the non-TTY fallback. The hook/Shell wiring is thin glue over these (the same
 * split as useOrientation: pure decision tested here, render verified live — unit renders can't
 * see flex/overflow truth anyway).
 */

import { describe, expect, it } from 'vitest';
import { MIN_TERMINAL_COLUMNS, MIN_TERMINAL_ROWS } from '../../src/components/App.js';
import { clampModalWidth } from '../../src/hooks/useTerminalSize.js';

describe('clampModalWidth', () => {
  it('keeps the design width on a wide terminal', () => {
    expect(clampModalWidth(64, 120)).toBe(64);
    expect(clampModalWidth(56, 80)).toBe(56);
  });

  it('clamps to columns − 2 when the terminal is narrower than the design width', () => {
    expect(clampModalWidth(64, 60)).toBe(58);
    expect(clampModalWidth(56, 50)).toBe(48);
  });

  it('never goes below the modal floor, even on an absurdly narrow terminal', () => {
    expect(clampModalWidth(64, 10)).toBe(24);
  });
});

describe('min-terminal-size floor', () => {
  it('sits below the 24×80 non-TTY fallback so piped/CI renders never trip the guard', () => {
    expect(MIN_TERMINAL_COLUMNS).toBeLessThanOrEqual(80);
    expect(MIN_TERMINAL_ROWS).toBeLessThanOrEqual(24);
  });
});
