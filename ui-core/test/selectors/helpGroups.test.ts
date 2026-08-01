/**
 * helpGroups — shared buildHelpGroups from resolved bindings + keymap registry.
 */

import { describe, expect, it } from 'vitest';
import { resolveBindings } from '../src/input/bindings.js';
import { createKeymapRegistry } from '../src/input/keymapRegistry.js';
import { buildHelpGroups } from '../src/selectors/helpGroups.js';

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
