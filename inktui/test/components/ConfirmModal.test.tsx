/**
 * ConfirmModal test — THE reference transient-mode test idiom every modal-ish chunk (C8/C12/C14)
 * copies. The recipe (copy alongside the mode you build):
 *  1. Build the C4 input stores ({@link createInputStores}) with a panel focused, so there is a real
 *     prior focus to restore.
 *  2. Render the {@link Overlay} inside the providers plus {@link useRootInput} (the production path),
 *     with a dev trigger that calls `modes.enter(yourMode(...))`.
 *  3. Drive it with simulated keys: trigger → assert the surface paints → key → assert the mode's
 *     intent fired (capture) → dismiss → assert the overlay is gone AND prior focus is restored.
 *  4. Assert a global chord does NOT fire while the modal is up (exclusive capture).
 *
 * Asserting on the frame is a real assertion because the surface is a pure function of its props.
 */

import { render } from 'ink-testing-library';
import type { JSX } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { type ConfirmChoice, confirmMode } from '../../src/components/ConfirmModal.js';
import { Overlay } from '../../src/components/Overlay.js';
import { InputStoresProvider } from '../../src/hooks/useInputStores.js';
import { useRootInput } from '../../src/hooks/useRootInput.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import { selectActiveMode } from '../../src/input/modeStore.js';

const ESC = '\x1b';

/** Let Ink flush a render + post-render effects. */
async function tick(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 20));
}

/** Runs the root input loop inside the providers (so simulated keys go through the real dispatcher). */
function RootInput(): null {
  useRootInput();
  return null;
}

/** The harness: the overlay + root loop inside the providers. The caller enters the mode imperatively
 * via the store handle before/after render (mirrors a real trigger firing `modes.enter(...)`). */
function Harness({
  stores,
}: {
  readonly stores: ReturnType<typeof createInputStores>;
}): JSX.Element {
  return (
    <InputStoresProvider value={stores}>
      <RootInput />
      <Overlay />
    </InputStoresProvider>
  );
}

/** Build stores with the tickets panel focused (the focus to restore), and a confirm mode entered. */
function setup(onChoose: (c: ConfirmChoice) => void) {
  const stores = createInputStores(['tickets'], 'tickets');
  const enter = () =>
    stores.modes.getState().enter(confirmMode(stores.modes, { message: 'Delete it?', onChoose }));
  return { stores, enter };
}

describe('ConfirmModal — reference transient mode', () => {
  it('opens, paints the dialog, captures the choice key, dismisses, and restores focus', async () => {
    const onChoose = vi.fn();
    const { stores, enter } = setup(onChoose);
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    await tick();
    expect(lastFrame()).not.toContain('Delete it?'); // nothing up yet

    enter();
    await tick();
    expect(lastFrame()).toContain('Delete it?'); // the modal painted
    expect(selectActiveMode(stores.modes)?.id).toBe('confirm');

    // 'y' is the mode's confirm chord → captured by layer 0.
    stdin.write('y');
    await tick();
    expect(onChoose).toHaveBeenCalledWith('confirm');
    expect(selectActiveMode(stores.modes)).toBeNull(); // dismissed
    expect(lastFrame()).not.toContain('Delete it?'); // overlay gone
    expect(stores.focus.getState().intendedId).toBe('tickets'); // prior focus restored
  });

  it('Esc dismisses (no confirm), restoring focus', async () => {
    const onChoose = vi.fn();
    const { stores, enter } = setup(onChoose);
    const { stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();
    stdin.write(ESC);
    await tick();
    expect(onChoose).toHaveBeenCalledWith('dismiss');
    expect(selectActiveMode(stores.modes)).toBeNull();
    expect(stores.focus.getState().intendedId).toBe('tickets');
  });

  it('captures exclusively: ctrl+f does NOT focus chat while the modal is up', async () => {
    const onChoose = vi.fn();
    const { stores, enter } = setup(onChoose);
    const { stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();
    // ctrl+f (\x06) would normally focus chat (a global chord); under the capturing modal it must not.
    stdin.write('\x06');
    await tick();
    expect(stores.focus.getState().intendedId).toBe('tickets'); // focus unmoved
    expect(selectActiveMode(stores.modes)?.id).toBe('confirm'); // modal still up
    expect(onChoose).not.toHaveBeenCalled();
  });
});
