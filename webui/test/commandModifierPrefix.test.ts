/** commandModifierPrefix + desktopKeybindHints track settings.modifier. */

import { describe, expect, it } from 'vitest';
import {
  commandModifierPrefix,
  desktopKeybindHints,
} from '../src/commandModifierPrefix.js';
import { buildWebHelpGroups } from '../src/components/modals/HelpDialog.js';

describe('commandModifierPrefix', () => {
  it('maps alt/ctrl/both to A-/C-/A-/C-', () => {
    expect(commandModifierPrefix('alt')).toBe('A-');
    expect(commandModifierPrefix('ctrl')).toBe('C-');
    expect(commandModifierPrefix('both')).toBe('A-/C-');
  });
});

describe('desktopKeybindHints', () => {
  it('prefixes chords from the live modifier (default settings use alt)', () => {
    const hints = desktopKeybindHints('alt');
    expect(hints.map((h) => h.chord)).toEqual([
      'A-1-0',
      'A-space',
      'A-hl',
      'A-S-jk',
      'A-s',
      'A-t',
      'A-p',
      'A-g',
      'A-w',
      'A-o',
      'C-n',
      '?',
    ]);
  });

  it('uses C- when modifier is ctrl', () => {
    expect(desktopKeybindHints('ctrl').find((h) => h.desc === 'spawn')?.chord).toBe('C-s');
  });
});

describe('buildWebHelpGroups', () => {
  it('mirrors KeybindBar prefix for the Global group', () => {
    const keys = buildWebHelpGroups('both').flatMap((g) => g.entries.map((e) => e.key));
    expect(keys).toContain('A-/C-s');
    expect(keys).toContain('A-/C-S-j / A-/C-S-k');
    expect(keys).toContain('A-/C-S-1–9');
    expect(keys).toContain('?');
  });
});
