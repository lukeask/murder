/** commandModifierPrefix + desktopKeybindHints track settings.modifier + overrides. */

import { describe, expect, it } from 'vitest';
import {
  commandModifierPrefix,
  desktopKeybindHints,
} from '../src/commandModifierPrefix.js';
import { buildWebHelpGroups } from '../src/components/modals/HelpDialog.js';
import { spawnDialogHints } from '../src/keybindModeHints.js';
import { WEB_PANEL_KEYMAPS } from '../src/panelHelpKeymaps.js';

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
    expect(hints.find((h) => h.desc === 'panels')?.chord).toMatch(/A-1/);
    expect(hints.find((h) => h.desc === 'chat')?.chord).toMatch(/A-.*space/);
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

  it('includes panel keymap hints when a rail panel is focused', () => {
    const chat = desktopKeybindHints('alt', {}, null);
    const onCrows = desktopKeybindHints('alt', {}, 'crows');
    expect(onCrows.some((h) => h.desc === 'next crow' || h.desc === 'favorite')).toBe(true);
    expect(chat.some((h) => h.desc === 'next crow')).toBe(false);
    expect(onCrows.some((h) => h.desc === 'help')).toBe(true);
  });

  it('includes tree g jump when tree is focused', () => {
    const onTree = desktopKeybindHints('alt', {}, 'tree');
    expect(onTree.some((h) => h.desc === 'jump (g)')).toBe(true);
  });

  it('merges stage scroll/goto chords when stageSurface is set', () => {
    const doc = desktopKeybindHints('alt', {}, null, undefined, null, 'doc');
    expect(doc.some((h) => h.desc === 'scroll down')).toBe(true);
    expect(doc.some((h) => h.desc === 'page down')).toBe(true);
    expect(doc.some((h) => h.desc === 'go to line')).toBe(true);
    // Chat globals remain (target / help).
    expect(doc.some((h) => h.chord === ':help' || h.desc === 'help')).toBe(true);

    const transcript = desktopKeybindHints('alt', {}, null, undefined, null, 'transcript');
    expect(transcript.some((h) => h.desc === 'newer')).toBe(true);
    expect(transcript.some((h) => h.desc === 'older')).toBe(true);
  });

  it('modeHints replace panel keys (nav trio + mode)', () => {
    const hints = desktopKeybindHints(
      'alt',
      {},
      'crows',
      undefined,
      spawnDialogHints('harness'),
    );
    expect(hints.some((h) => h.desc === 'next crow')).toBe(false);
    expect(hints.some((h) => h.desc === 'help')).toBe(false);
    expect(hints.some((h) => h.desc === 'nav' || h.desc === 'confirm')).toBe(true);
    expect(hints.some((h) => h.desc === 'cancel')).toBe(true);
    expect(hints.some((h) => h.desc === 'panels')).toBe(true);
  });
});

describe('buildWebHelpGroups', () => {
  it('mirrors live binding prefix for the Global group', () => {
    const keys = buildWebHelpGroups('both').flatMap((g) => g.entries.map((e) => e.key));
    expect(keys).toContain('A-s/C-s');
    expect(keys.some((k) => k.includes('S-j'))).toBe(true);
    expect(keys).toContain('?');
  });

  it('includes tree jump and stage scroll/goto groups', () => {
    const groups = buildWebHelpGroups();
    const tree = groups.find((g) => g.title === 'Tree panel');
    expect(tree?.entries.some((e) => e.description === 'jump (g)')).toBe(true);
    expect(groups.some((g) => g.title === 'Stage document')).toBe(true);
    expect(groups.some((g) => g.title === 'Stage transcript')).toBe(true);
    const docKeys = groups
      .find((g) => g.title === 'Stage document')
      ?.entries.map((e) => e.description);
    expect(docKeys).toContain('page down');
    expect(docKeys).toContain('go to line');
  });
});

describe('WEB_PANEL_KEYMAPS.tree', () => {
  it('declares g jump', () => {
    expect(WEB_PANEL_KEYMAPS.tree?.some((e) => e.intent === 'startG')).toBe(true);
  });
});
