/**
 * Bar view-model tests — the pure transforms the top/bottom bars render from (rule 2). Asserting the
 * selectors directly keeps the bar formatting (subscript labels, hint sourcing) tested without Ink.
 */

import { describe, expect, it } from 'vitest';
import { DEFAULT_BINDINGS } from '../../src/input/bindings.js';
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
});
