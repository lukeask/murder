/**
 * Bar view-model tests — the pure transforms the top/bottom bars render from (rule 2). Asserting the
 * selectors directly keeps the bar formatting (subscript labels, hint sourcing) tested without Ink.
 */

import { describe, expect, it } from 'vitest';
import { DEFAULT_BINDINGS, resolveBindings } from '../../src/input/bindings.js';
import { CHAT_FOCUS } from '../../src/input/focusStore.js';
import type { Keymap } from '../../src/input/keymap.js';
import type { PanelId } from '../../src/input/panels.js';
import { selectBottomBar, selectTopBar } from '../../src/selectors/barSelectors.js';

describe('selectTopBar', () => {
  it('labels every panel with its subscript digit, in screen order', () => {
    const labels = selectTopBar(new Set<PanelId>());
    expect(labels.map((l) => l.text)).toEqual([
      'plans₁',
      'notes₂',
      'reports₃',
      'tickets₄',
      'history₅',
      'usage₉',
      'crows₀',
    ]);
  });

  it('marks only the visible panels active', () => {
    const labels = selectTopBar(new Set<PanelId>(['plans', 'crows']));
    const active = labels.filter((l) => l.active).map((l) => l.id);
    expect(active).toEqual(['plans', 'crows']);
  });
});

describe('selectBottomBar', () => {
  const keymap: Keymap<'open' | 'star'> = [
    { chord: { input: 'o' }, intent: 'open', description: 'open doc' },
    { chord: { key: { return: true } }, intent: 'star', description: 'star' },
  ];

  it('shows only the global hints when chat is focused', () => {
    const hints = selectBottomBar(CHAT_FOCUS, undefined, DEFAULT_BINDINGS);
    expect(hints.every((h) => h.description !== 'open doc')).toBe(true);
    expect(hints.length).toBeGreaterThan(0);
  });

  it('appends the focused panel keys, naming special keys', () => {
    const hints = selectBottomBar('plans', keymap, DEFAULT_BINDINGS);
    const descriptions = hints.map((h) => h.description);
    expect(descriptions).toContain('open doc');
    expect(descriptions).toContain('star');
    // A printable chord renders its char; a key-only chord renders the special-key name.
    expect(hints.find((h) => h.description === 'open doc')?.key).toBe('o');
    expect(hints.find((h) => h.description === 'star')?.key).toBe('return');
  });

  it("shows a command-modified panel key's modifier, varying A-↔C- with the configured modifier", () => {
    // A panel that binds a key through the registry (e.g. star = the command modifier + `f`) must show
    // the modifier in its hint — a bare `f` would read as un-pressable. The prefix tracks the user's
    // configured modifier exactly like the global/nav hints.
    const starKeymap: Keymap<'star'> = [
      { chord: { input: 'f', key: { meta: true } }, intent: 'star', description: 'favorite' },
    ];
    const alt = selectBottomBar('plans', starKeymap, resolveBindings('alt', false, {}));
    expect(alt.find((h) => h.description === 'favorite')?.key).toBe('A-f');

    const ctrlKeymap: Keymap<'star'> = [
      { chord: { input: 'f', key: { ctrl: true } }, intent: 'star', description: 'favorite' },
    ];
    const ctrl = selectBottomBar('plans', ctrlKeymap, resolveBindings('ctrl', true, {}));
    expect(ctrl.find((h) => h.description === 'favorite')?.key).toBe('C-f');
  });

  it('pins a right-aligned help hint (item 12) on the normal bar, labelled from the binding', () => {
    const hints = selectBottomBar('plans', keymap, DEFAULT_BINDINGS);
    const help = hints.find((h) => h.description === 'help');
    expect(help).toBeDefined();
    expect(help?.align).toBe('right');
    // The label is the resolved global.keyHelp binding (a plain ?).
    expect(help?.key).toBe(DEFAULT_BINDINGS.label('global.keyHelp'));
  });

  it('disambiguates the help hint while chat is focused (a bare ? would type into the input)', () => {
    // First-run UX: in chat focus the dispatcher never steals `?`, so the hint must not present a
    // bare `?` as pressable — it prefixes the nav-out chord ("move focus, then ?").
    const help = selectBottomBar(CHAT_FOCUS, undefined, DEFAULT_BINDINGS).find(
      (h) => h.description === 'help',
    );
    expect(help).toBeDefined();
    expect(help?.align).toBe('right');
    expect(help?.key).toBe(`A-hjkl ${DEFAULT_BINDINGS.label('global.keyHelp')}`);
  });

  it('omits the help hint when a mode owns the bar (a modal has its own keys)', () => {
    const hints = selectBottomBar('plans', keymap, DEFAULT_BINDINGS, [
      { key: 'esc', description: 'quit' },
    ]);
    expect(hints.find((h) => h.description === 'help')).toBeUndefined();
  });

  it('mode hints replace the panel keys (globals still lead) when a mode owns the bar', () => {
    const modeHints = [
      { key: 'j/k', description: 'nav' },
      { key: 'esc', description: 'cancel' },
    ];
    const hints = selectBottomBar('plans', keymap, DEFAULT_BINDINGS, modeHints);
    const descriptions = hints.map((h) => h.description);
    // The mode's hints are present…
    expect(descriptions).toContain('nav');
    expect(descriptions).toContain('cancel');
    // …and the focused panel's keys are NOT (the mode captures input).
    expect(descriptions).not.toContain('open doc');
    expect(descriptions).not.toContain('star');
    // Globals still lead.
    expect(hints.length).toBeGreaterThan(modeHints.length);
  });
});
