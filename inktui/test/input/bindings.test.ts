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

  it('labels read as M-<key> (space spelled out)', () => {
    expect(b.label('panel.star')).toBe('M-f');
    expect(b.label('global.focusChat')).toBe('M-space');
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
    expect(b.label('panel.star')).toBe('M-f');
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
    expect(b.label('panel.star')).toBe('M-f/C-f');
  });

  it('collapses to alt-only when ctrl is unavailable', () => {
    const b = resolveBindings('both', false, {});
    expect(b.chordsFor('panel.star')).toEqual([{ input: 'f', key: { meta: true } }]);
    expect(b.matches('panel.star', 'f', makeKey({ ctrl: true }))).toBe(false);
    expect(b.label('panel.star')).toBe('M-f');
  });
});

describe('resolveBindings — overrides', () => {
  it('rebinds a command action to a different key char', () => {
    const b = resolveBindings('alt', false, { 'panel.star': 'b' });
    expect(b.chordsFor('panel.star')).toEqual([{ input: 'b', key: { meta: true } }]);
    expect(b.matches('panel.star', 'b', makeKey({ meta: true }))).toBe(true);
    expect(b.matches('panel.star', 'f', makeKey({ meta: true }))).toBe(false);
    expect(b.label('panel.star')).toBe('M-b');
  });

  it('an override expands under both too', () => {
    const b = resolveBindings('both', true, { 'global.tmux': 'z' });
    expect(b.chordsFor('global.tmux')).toEqual([
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

  it('global.keyHelp is a plain ? — modifier-independent and labelled as the bare key', () => {
    const alt = resolveBindings('alt', false, {});
    const ctrl = resolveBindings('ctrl', true, {});
    // A plain binding ignores the modifier: ? under both alt and ctrl, no M-/C- prefix.
    expect(alt.label('global.keyHelp')).toBe('?');
    expect(ctrl.label('global.keyHelp')).toBe('?');
    expect(alt.matches('global.keyHelp', '?', makeKey())).toBe(true);
    expect(ctrl.matches('global.keyHelp', '?', makeKey())).toBe(true);
  });

  it('the item-9 super-chords are command actions (track the modifier)', () => {
    const alt = resolveBindings('alt', false, {});
    expect(alt.label('global.cycleTargetPrev')).toBe('M-h');
    expect(alt.label('global.cycleTargetNext')).toBe('M-l');
    expect(alt.label('global.toggleTargetPane')).toBe('M-w');
    expect(alt.matches('global.toggleTargetPane', 'w', makeKey({ meta: true }))).toBe(true);
    const ctrl = resolveBindings('ctrl', true, {});
    expect(ctrl.label('global.toggleTargetPane')).toBe('C-w');
  });
});
