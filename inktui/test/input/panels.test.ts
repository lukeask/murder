/**
 * panels.ts mapping tests — the digit→panel single source of truth, with the history panel on
 * ctrl+5 (left rail, after tickets in screen order) and the transit panel on ctrl+8 (right rail,
 * before usage in screen order). Reserved digits 6–7 stay unbound.
 */

import { describe, expect, it } from 'vitest';
import { DIGIT_TO_PANEL, PANEL_IDS, PANELS, panelForDigit } from '../../src/input/panels.js';

describe('panels mapping', () => {
  it('maps digit 5 to the history panel (left rail)', () => {
    expect(panelForDigit('5')).toBe('history');
    expect(DIGIT_TO_PANEL[5]).toBe('history');
    const placement = PANELS.find((p) => p.id === 'history');
    expect(placement).toEqual({ id: 'history', digit: 5, region: 'left' });
  });

  it('maps digit 8 to the transit panel (right rail, before usage)', () => {
    expect(panelForDigit('8')).toBe('transit');
    expect(DIGIT_TO_PANEL[8]).toBe('transit');
    const placement = PANELS.find((p) => p.id === 'transit');
    expect(placement).toEqual({ id: 'transit', digit: 8, region: 'right', label: 'tree' });
  });

  it('places history after tickets and transit before usage in screen order', () => {
    expect(PANEL_IDS).toEqual([
      'plans',
      'notes',
      'reports',
      'tickets',
      'history',
      'transit',
      'usage',
      'crows',
    ]);
  });

  it('leaves digits 6–7 unbound (reserved → no-op)', () => {
    expect(panelForDigit('6')).toBeNull();
    expect(panelForDigit('7')).toBeNull();
  });

  it('still maps the existing left/right digits', () => {
    expect(panelForDigit('1')).toBe('plans');
    expect(panelForDigit('4')).toBe('tickets');
    expect(panelForDigit('9')).toBe('usage');
    expect(panelForDigit('0')).toBe('crows');
  });
});
