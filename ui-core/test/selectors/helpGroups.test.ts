/**
 * helpGroups — shared buildHelpGroups from resolved bindings + keymap registry.
 */

import { describe, expect, it } from 'vitest';
import { resolveBindings } from '@murder/ui-core/input/bindings.js';
import { createKeymapRegistry } from '@murder/ui-core/input/keymapRegistry.js';
import {
  buildHelpGroups,
  paginateHelp,
  type HelpGroup,
} from '@murder/ui-core/selectors/helpGroups.js';

describe('buildHelpGroups', () => {
  it('labels global binds from the resolved bindings (tracks the modifier)', () => {
    const registry = createKeymapRegistry();
    const altGroups = buildHelpGroups(resolveBindings('alt', false, {}), registry);
    const ctrlGroups = buildHelpGroups(resolveBindings('ctrl', true, {}), registry);
    const altGlobal = altGroups.find((g) => g.title === 'Global');
    const ctrlGlobal = ctrlGroups.find((g) => g.title === 'Global');
    expect(altGlobal?.entries.find((e) => e.description === 'spawn')?.key).toBe('A-s');
    expect(ctrlGlobal?.entries.find((e) => e.description === 'spawn')?.key).toBe('C-s');
    expect(altGlobal?.entries.find((e) => e.description === 'help')?.key).toBe('?');
  });

  it('includes workspace chords in Global', () => {
    const groups = buildHelpGroups(resolveBindings('alt', false, {}), createKeymapRegistry());
    const global = groups.find((g) => g.title === 'Global');
    expect(global?.entries.some((e) => e.description.includes('workspace'))).toBe(true);
  });

  it('includes a panel group only for registered panels', () => {
    const registry = createKeymapRegistry();
    expect(
      buildHelpGroups(resolveBindings('alt', false, {}), registry).find(
        (g) => g.title === 'Plans panel',
      ),
    ).toBeUndefined();

    registry.getState().register('plans', {
      keymap: [{ chord: { input: 'o' }, intent: 'open', description: 'open doc' }],
      onIntent: () => {},
    });
    const plans = buildHelpGroups(resolveBindings('alt', false, {}), registry).find(
      (g) => g.title === 'Plans panel',
    );
    expect(plans?.entries).toEqual([{ key: 'o', description: 'open doc' }]);
  });

  it('always includes Modals and Commands', () => {
    const groups = buildHelpGroups(resolveBindings('alt', false, {}), createKeymapRegistry());
    expect(groups.find((g) => g.title === 'Modals')).toBeDefined();
    const commands = groups.find((g) => g.title === 'Commands');
    expect(commands?.entries.map((e) => e.key)).toContain(':help');
    expect(commands?.entries.map((e) => e.key)).toContain(':workflows');
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
    for (const page of pages) {
      const rows = page.reduce((acc, g) => acc + g.entries.length, 0);
      expect(rows).toBeLessThanOrEqual(6);
    }
  });
});
