/**
 * bindings tests — the pure resolution of the central registry. Covers the three modifier choices,
 * the ctrl→alt degradation when ctrl is unavailable, the `both` two-chord expansion, per-action key
 * overrides, labels, and the `isCommandModified` gate. No React, no rendering.
 */

import { describe, expect, it } from 'vitest';
import {
  ACTION_IDS,
  ACTIONS,
  DEFAULT_BINDINGS,
  resolveBindings,
} from '../../src/input/bindings.js';
import { chordMatches } from '../../src/input/keymap.js';
import { makeKey } from './key.js';

describe("resolveBindings — alt (today's default)", () => {
  const b = resolveBindings('alt', false, {});

  it('binds command actions to meta+key', () => {
    expect(b.chordsFor('panel.star')).toEqual([{ input: 'f', key: { meta: true } }]);
    expect(b.chordsFor('global.spawn')).toEqual([{ input: 's', key: { meta: true } }]);
    expect(b.chordsFor('global.focusChat')).toEqual([{ input: ' ', key: { meta: true } }]);
  });

  it('matches a meta+key event but not the bare key or ctrl+key', () => {
    expect(b.matches('panel.star', 'f', makeKey({ meta: true }))).toBe(true);
    expect(b.matches('panel.star', 'f', makeKey())).toBe(false);
    expect(b.matches('panel.star', 'f', makeKey({ ctrl: true }))).toBe(false);
    expect(b.matches('panel.star', 'g', makeKey({ meta: true }))).toBe(false);
  });

  it('isCommandModified is meta only', () => {
    expect(b.isCommandModified(makeKey({ meta: true }))).toBe(true);
    expect(b.isCommandModified(makeKey({ ctrl: true }))).toBe(false);
    expect(b.isCommandModified(makeKey())).toBe(false);
  });

  it('labels read as A-<key> (alt prefix, space spelled out)', () => {
    expect(b.label('panel.star')).toBe('A-f');
    expect(b.label('global.focusChat')).toBe('A-space');
  });

  it('DEFAULT_BINDINGS equals resolveBindings(alt,false,{})', () => {
    expect(DEFAULT_BINDINGS.chordsFor('panel.star')).toEqual(b.chordsFor('panel.star'));
    expect(DEFAULT_BINDINGS.isCommandModified(makeKey({ meta: true }))).toBe(true);
  });
});

describe('resolveBindings — ctrl', () => {
  it('binds to ctrl+key and matches ctrl events when ctrl is available', () => {
    const b = resolveBindings('ctrl', true, {});
    expect(b.chordsFor('panel.star')).toEqual([{ input: 'f', key: { ctrl: true } }]);
    expect(b.matches('panel.star', 'f', makeKey({ ctrl: true }))).toBe(true);
    expect(b.matches('panel.star', 'f', makeKey({ meta: true }))).toBe(false);
    expect(b.isCommandModified(makeKey({ ctrl: true }))).toBe(true);
    expect(b.isCommandModified(makeKey({ meta: true }))).toBe(false);
    expect(b.label('panel.star')).toBe('C-f');
  });

  it('degrades to alt when ctrl is unavailable', () => {
    const b = resolveBindings('ctrl', false, {});
    expect(b.chordsFor('panel.star')).toEqual([{ input: 'f', key: { meta: true } }]);
    expect(b.matches('panel.star', 'f', makeKey({ meta: true }))).toBe(true);
    expect(b.matches('panel.star', 'f', makeKey({ ctrl: true }))).toBe(false);
    expect(b.label('panel.star')).toBe('A-f');
  });
});

describe('resolveBindings — both', () => {
  it('expands to two chords (alt + ctrl) and matches either when ctrl is available', () => {
    const b = resolveBindings('both', true, {});
    expect(b.chordsFor('panel.star')).toEqual([
      { input: 'f', key: { meta: true } },
      { input: 'f', key: { ctrl: true } },
    ]);
    expect(b.matches('panel.star', 'f', makeKey({ meta: true }))).toBe(true);
    expect(b.matches('panel.star', 'f', makeKey({ ctrl: true }))).toBe(true);
    expect(b.isCommandModified(makeKey({ meta: true }))).toBe(true);
    expect(b.isCommandModified(makeKey({ ctrl: true }))).toBe(true);
    expect(b.label('panel.star')).toBe('A-f/C-f');
  });

  it('collapses to alt-only when ctrl is unavailable', () => {
    const b = resolveBindings('both', false, {});
    expect(b.chordsFor('panel.star')).toEqual([{ input: 'f', key: { meta: true } }]);
    expect(b.matches('panel.star', 'f', makeKey({ ctrl: true }))).toBe(false);
    expect(b.label('panel.star')).toBe('A-f');
  });
});

describe('resolveBindings — overrides', () => {
  it('rebinds a command action to a different key char', () => {
    const b = resolveBindings('alt', false, { 'panel.star': 'b' });
    expect(b.chordsFor('panel.star')).toEqual([{ input: 'b', key: { meta: true } }]);
    expect(b.matches('panel.star', 'b', makeKey({ meta: true }))).toBe(true);
    expect(b.matches('panel.star', 'f', makeKey({ meta: true }))).toBe(false);
    expect(b.label('panel.star')).toBe('A-b');
  });

  it('an override expands under both too', () => {
    const b = resolveBindings('both', true, { 'global.cycleChatView': 'z' });
    expect(b.chordsFor('global.cycleChatView')).toEqual([
      { input: 'z', key: { meta: true } },
      { input: 'z', key: { ctrl: true } },
    ]);
  });
});

describe('ACTIONS table', () => {
  it('every action id resolves to at least one chord', () => {
    const b = resolveBindings('alt', false, {});
    for (const id of ACTION_IDS) {
      expect(b.chordsFor(id).length).toBeGreaterThan(0);
    }
  });

  it('ACTION_IDS matches the ACTIONS keys', () => {
    expect(new Set(ACTION_IDS)).toEqual(new Set(Object.keys(ACTIONS)));
  });

  it('panel.historyResume is a plain `r` — modifier-independent (panel-scoped chord)', () => {
    const b = resolveBindings('alt', false, {});
    // A plain chord matches the bare key regardless of the active modifier setting.
    expect(b.matches('panel.historyResume', 'r', makeKey())).toBe(true);
    expect(b.matches('panel.historyResume', 'g', makeKey())).toBe(false);
    expect(b.label('panel.historyResume')).toBe('r');
  });

  it('global.keyHelp is a plain ? — modifier-independent and labelled as the bare key', () => {
    const alt = resolveBindings('alt', false, {});
    const ctrl = resolveBindings('ctrl', true, {});
    // A plain binding ignores the modifier: ? under both alt and ctrl, no A-/C- prefix.
    expect(alt.label('global.keyHelp')).toBe('?');
    expect(ctrl.label('global.keyHelp')).toBe('?');
    expect(alt.matches('global.keyHelp', '?', makeKey())).toBe(true);
    expect(ctrl.matches('global.keyHelp', '?', makeKey())).toBe(true);
  });

  it('the item-9 super-chords are command actions (track the modifier)', () => {
    const alt = resolveBindings('alt', false, {});
    expect(alt.label('global.cycleTargetPrev')).toBe('A-h');
    expect(alt.label('global.cycleTargetNext')).toBe('A-l');
    expect(alt.label('global.toggleTargetPane')).toBe('A-w');
    expect(alt.matches('global.toggleTargetPane', 'w', makeKey({ meta: true }))).toBe(true);
    const ctrl = resolveBindings('ctrl', true, {});
    expect(ctrl.label('global.toggleTargetPane')).toBe('C-w');
  });
});

describe('global.repaint — plain ctrl+r chord (manual redraw)', () => {
  it('resolves to ctrl+r under every modifier (plain = modifier-independent) and labels C-r', () => {
    for (const modifier of ['alt', 'ctrl', 'both'] as const) {
      const bindings = resolveBindings(modifier, true, {});
      expect(bindings.chordsFor('global.repaint')).toEqual([{ input: 'r', key: { ctrl: true } }]);
      expect(bindings.matches('global.repaint', 'r', makeKey({ ctrl: true }))).toBe(true);
      expect(bindings.matches('global.repaint', 'r', makeKey())).toBe(false);
      expect(bindings.label('global.repaint')).toBe('C-r');
    }
  });

  it('is not rebindable (a fixed muscle-memory chord)', () => {
    expect(ACTIONS['global.repaint'].rebindable).toBe(false);
  });
});

describe('global.murder — plain ctrl+m chord with a label override', () => {
  it('resolves to the ctrl+return collision chord under every modifier (plain = modifier-independent)', () => {
    for (const modifier of ['alt', 'ctrl', 'both'] as const) {
      const bindings = resolveBindings(modifier, true, {});
      expect(bindings.chordsFor('global.murder')).toEqual([{ key: { ctrl: true, return: true } }]);
      // The lifted side-channel event ({ ctrl, return }, empty input) matches; plain Enter doesn't.
      expect(bindings.matches('global.murder', '', makeKey({ ctrl: true, return: true }))).toBe(
        true,
      );
      expect(bindings.matches('global.murder', '', makeKey({ return: true }))).toBe(false);
    }
  });

  it('labels as C-m (the override), not the mechanical C-return', () => {
    expect(resolveBindings('alt', false, {}).label('global.murder')).toBe('C-m');
  });
});

describe('shift-carrying command chords (workspace prerequisite — commandChord semantics)', () => {
  // The `workspace.*` actions carry `shift: true`; these tests pin the CHORD SHAPES a shifted
  // command binding resolves to and the events they match, via chordMatches — the exact predicate
  // resolveBindings uses.
  const metaFlavor = { input: 'J', key: { meta: true, shift: true } } as const;
  const ctrlFlavor = { input: 'j', key: { ctrl: true, shift: true } } as const;

  it('meta flavor matches the legacy alt+shift+j event (ESC J → uppercase input + shift)', () => {
    expect(chordMatches(metaFlavor, 'J', makeKey({ meta: true, shift: true }))).toBe(true);
    // Bare alt+j (lowercase, no shift) must not match.
    expect(chordMatches(metaFlavor, 'j', makeKey({ meta: true }))).toBe(false);
  });

  it('ctrl flavor matches the kitty side-channel event (unshifted char + shift bit)', () => {
    expect(chordMatches(ctrlFlavor, 'j', makeKey({ ctrl: true, shift: true }))).toBe(true);
    // Bare ctrl+j must not match the shifted chord.
    expect(chordMatches(ctrlFlavor, 'j', makeKey({ ctrl: true }))).toBe(false);
  });
});

describe('workspace.* — shifted command chords', () => {
  it('workspace.next resolves to the meta/ctrl shift-carrying shapes under ctrl', () => {
    const bindings = resolveBindings('ctrl', true, {});
    expect(bindings.chordsFor('workspace.next')).toEqual([
      { input: 'j', key: { ctrl: true, shift: true } },
    ]);
    expect(bindings.matches('workspace.next', 'j', makeKey({ ctrl: true, shift: true }))).toBe(
      true,
    );
    expect(bindings.matches('workspace.next', 'j', makeKey({ ctrl: true }))).toBe(false);
    expect(bindings.label('workspace.next')).toBe('C-S-j');
  });

  it('workspace.jump.5 resolves to ctrl+shift+5', () => {
    const bindings = resolveBindings('ctrl', true, {});
    expect(bindings.chordsFor('workspace.jump.5')).toEqual([
      { input: '5', key: { ctrl: true, shift: true } },
    ]);
    expect(bindings.matches('workspace.jump.5', '5', makeKey({ ctrl: true, shift: true }))).toBe(
      true,
    );
    expect(bindings.label('workspace.jump.5')).toBe('C-S-5');
  });
});
