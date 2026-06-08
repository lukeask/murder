/**
 * Integration test for the C4 input/focus backbone via {@link FocusDemo} and `ink-testing-library`,
 * driving real key sequences through the one root dispatcher. Proves, end to end:
 *  - the focus highlight starts on chat (re-home home);
 *  - `ctrl+vim` (`ctrl+l`) moves the highlight to the geometric neighbour;
 *  - hiding the focused panel re-homes the highlight to chat (the derived invariant);
 *  - a focused panel's declared key fires its intent, and an unfocused panel's does not.
 *
 * Note on keys: a terminal can't byte-encode `ctrl+<digit>`, so panel toggling is exercised through
 * the panel store directly (its own unit test covers toggle; the dispatcher test covers the
 * `ctrl+<n>` routing). Control chords that *are* representable (`ctrl+l` = \x0c, `ctrl+f` = \x06)
 * drive nav/focus here.
 */

import { render } from 'ink-testing-library';
import { describe, expect, it } from 'vitest';
import { FocusDemo } from '../../src/components/FocusDemo.js';
import { createInputStores } from '../../src/input/createInputStores.js';

const CTRL_L = '\x0c';
const CTRL_F = '\x06';

/** Let Ink flush a render + the post-layout measure effect after a key. */
async function tick(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 20));
}

describe('FocusDemo — input/focus backbone end to end', () => {
  it('starts with the chat input focused', async () => {
    const stores = createInputStores(['plans', 'tickets']);
    const { lastFrame } = render(<FocusDemo stores={stores} />);
    await tick();
    expect(lastFrame()).toContain('chat [focused]');
  });

  it('ctrl+l moves the highlight from plans to the tickets panel on its right', async () => {
    const stores = createInputStores(['plans', 'tickets']);
    stores.focus.getState().focus('plans');
    const { stdin, lastFrame } = render(<FocusDemo stores={stores} />);
    await tick(); // allow measure effect to register rects

    expect(lastFrame()).toContain('plans*');
    stdin.write(CTRL_L);
    await tick();
    expect(stores.focus.getState().intendedId).toBe('tickets');
    expect(lastFrame()).toContain('tickets*');
  });

  it('hiding the focused panel re-homes the highlight to chat (derived invariant)', async () => {
    const stores = createInputStores(['plans', 'tickets']);
    stores.focus.getState().focus('tickets');
    const { lastFrame, rerender } = render(<FocusDemo stores={stores} />);
    await tick();
    expect(lastFrame()).toContain('tickets*');

    // Hide the focused panel — touch only the panel store, never re-home imperatively.
    stores.panels.getState().hide('tickets');
    rerender(<FocusDemo stores={stores} />);
    await tick();

    expect(lastFrame()).toContain('chat [focused]');
    expect(stores.focus.getState().intendedId).toBe('tickets'); // intent preserved; focus derived
  });

  it('a focused panel runs its declared intent; an unfocused one does not', async () => {
    const stores = createInputStores(['plans', 'tickets']);
    stores.focus.getState().focus('plans');
    const { stdin, lastFrame } = render(<FocusDemo stores={stores} />);
    await tick();

    // Focused: 'a' fires plans' declared intent → counter bumps.
    stdin.write('a');
    await tick();
    expect(lastFrame()).toContain('plans* acted=1');

    // Move focus to chat; 'a' now goes to the input, not the panel → counter stays.
    stdin.write(CTRL_F);
    await tick();
    expect(lastFrame()).toContain('chat [focused]');
    stdin.write('a');
    await tick();
    expect(lastFrame()).toContain('acted=1'); // unchanged
  });
});
