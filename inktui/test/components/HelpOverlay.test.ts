/**
 * HelpOverlay tests — the pure pieces of the keybinding help overlay (item 12): the grouped entries
 * built from the resolved bindings + keymap registry, and the paging math. No Ink rendering.
 */

import { describe, expect, it } from 'vitest';
import { buildHelpGroups, type HelpGroup, paginateHelp } from '../../src/components/HelpOverlay.js';
import { resolveBindings } from '../../src/input/bindings.js';
import { createKeymapRegistry } from '../../src/input/keymapRegistry.js';

describe('buildHelpGroups', () => {
  it('labels global binds from the resolved bindings (tracks the modifier)', () => {
    const registry = createKeymapRegistry();
    const altGroups = buildHelpGroups(resolveBindings('alt', false, {}), registry);
    const ctrlGroups = buildHelpGroups(resolveBindings('ctrl', true, {}), registry);
    const altGlobal = altGroups.find((g) => g.title === 'Global');
    const ctrlGlobal = ctrlGroups.find((g) => g.title === 'Global');
    // The spawn bind reads A-s under alt, C-s under ctrl.
    expect(altGlobal?.entries.find((e) => e.description === 'spawn')?.key).toBe('A-s');
    expect(ctrlGlobal?.entries.find((e) => e.description === 'spawn')?.key).toBe('C-s');
    // The help bind is the plain ? under both.
    expect(altGlobal?.entries.find((e) => e.description === 'help')?.key).toBe('?');
  });

  it('includes a panel group only for registered panels, with their descriptions', () => {
    const registry = createKeymapRegistry();
    const beforeReg = buildHelpGroups(resolveBindings('alt', false, {}), registry);
    expect(beforeReg.find((g) => g.title === 'Plans panel')).toBeUndefined();

    registry.getState().register('plans', {
      keymap: [{ chord: { input: 'o' }, intent: 'open', description: 'open doc' }],
      onIntent: () => {},
    });
    const afterReg = buildHelpGroups(resolveBindings('alt', false, {}), registry);
    const plans = afterReg.find((g) => g.title === 'Plans panel');
    expect(plans?.entries).toEqual([{ key: 'o', description: 'open doc' }]);
  });

  it('always includes the Modals convention group', () => {
    const groups = buildHelpGroups(resolveBindings('alt', false, {}), createKeymapRegistry());
    expect(groups.find((g) => g.title === 'Modals')).toBeDefined();
  });

  it('includes the Commands group documenting the : / prefix surface', () => {
    const groups = buildHelpGroups(resolveBindings('alt', false, {}), createKeymapRegistry());
    const commands = groups.find((g) => g.title === 'Commands');
    expect(commands).toBeDefined();
    const keys = commands?.entries.map((e) => e.key) ?? [];
    expect(keys).toContain('/…');
    expect(keys).toContain(':help');
    expect(keys).toContain(':note <text>');
    expect(keys).toContain(':verbose / :compact / :tmux');
    expect(keys).toContain(':resume');
  });
});

describe('paginateHelp', () => {
  function group(title: string, n: number): HelpGroup {
    return {
      title,
      entries: Array.from({ length: n }, (_, i) => ({ key: `k${i}`, description: `d${i}` })),
    };
  }

  it('keeps everything on one page when it fits', () => {
    const pages = paginateHelp([group('A', 3), group('B', 2)], 10);
    expect(pages).toHaveLength(1);
    expect(pages[0]?.map((g) => g.title)).toEqual(['A', 'B']);
  });

  it('splits onto multiple pages when the entry count exceeds the page size', () => {
    const pages = paginateHelp([group('A', 5), group('B', 5)], 6);
    expect(pages.length).toBeGreaterThan(1);
    // Every page holds at most 6 entry rows.
    for (const page of pages) {
      const rows = page.reduce((acc, g) => acc + g.entries.length, 0);
      expect(rows).toBeLessThanOrEqual(6);
    }
  });

  it('splits an oversized single group across pages, repeating its heading', () => {
    const pages = paginateHelp([group('Big', 10)], 4);
    expect(pages.length).toBe(3);
    expect(pages[0]?.[0]?.title).toBe('Big');
    expect(pages[1]?.[0]?.title).toBe('Big (cont.)');
  });

  it('always returns at least one page', () => {
    expect(paginateHelp([], 10)).toHaveLength(1);
  });
});
