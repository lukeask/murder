/** commandModifierPrefix + desktopKeybindHints track settings.modifier + overrides. */

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
  it('emits live selectBottomBar chords for chat focus (alt)', () => {
    const hints = desktopKeybindHints('alt');
    const byDesc = Object.fromEntries(hints.map((h) => [h.desc, h.chord]));
    expect(byDesc.panels).toMatch(/A-1/);
    expect(byDesc.chat ?? byDesc['focus chat']).toBeDefined();
    expect(hints.some((h) => h.chord.includes('s') && h.desc.includes('spawn'))).toBe(true);
    expect(hints.some((h) => h.chord === ':help' || h.desc === 'help')).toBe(true);
  });

  it('uses C- when modifier is ctrl', () => {
    const spawn = desktopKeybindHints('ctrl').find((h) => h.desc === 'spawn');
    expect(spawn?.chord).toBe('C-s');
  });

  it('tracks key_overrides on rebindable actions', () => {
    const spawn = desktopKeybindHints('alt', { 'global.spawn': 'q' }).find(
      (h) => h.desc === 'spawn',
    );
    expect(spawn?.chord).toBe('A-q');
  });
});

describe('buildWebHelpGroups', () => {
  it('mirrors live binding prefix for the Global group', () => {
    const keys = buildWebHelpGroups('both').flatMap((g) => g.entries.map((e) => e.key));
    expect(keys).toContain('A-/C-s');
    expect(keys.some((k) => k.includes('S-j'))).toBe(true);
    expect(keys).toContain('?');
  });
});
