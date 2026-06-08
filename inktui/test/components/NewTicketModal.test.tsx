/**
 * NewTicketModal tests — verifies the `ctrl+t` new-ticket modal mode against the C7M idiom.
 *
 * Copy recipe (mirrors ConfirmModal.test.tsx — identical harness structure):
 *  1. Build input stores with a panel focused, enter the mode imperatively.
 *  2. Open → assert paint → type → submit → assert `ticket.quick_create` RPC fired → focus restored.
 *  3. Esc dismisses without firing RPC.
 *  4. Panel chord does NOT fire while the modal is up (exclusive capture).
 */

import { render } from 'ink-testing-library';
import type { JSX } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { NEW_TICKET_MODE_ID, newTicketMode } from '../../src/components/NewTicketModal.js';
import { Overlay } from '../../src/components/Overlay.js';
import { InputStoresProvider } from '../../src/hooks/useInputStores.js';
import { useRootInput } from '../../src/hooks/useRootInput.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import { selectActiveMode } from '../../src/input/modeStore.js';
import { createDialogActions } from '../../src/store/dialogs/dialogActions.js';

const ESC = '\x1b';

/** Let Ink flush a render + post-render effects. */
async function tick(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 20));
}

/** Runs the root input loop inside the providers. */
function RootInput(): null {
  useRootInput();
  return null;
}

/** The harness: the overlay + root loop inside the providers. */
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

/** Build stores with the tickets panel focused (prior focus to restore). */
function setup() {
  const stores = createInputStores(['tickets'], 'tickets');
  const bus = new FakeBusClient();

  bus.stubRpc('ticket.quick_create', {
    handled: true,
    ticket_id: 't-001',
    title: 'my ticket',
  });

  const actions = createDialogActions(bus);
  const enter = (opts = {}) =>
    stores.modes.getState().enter(newTicketMode(stores.modes, actions, opts));
  return { stores, bus, actions, enter };
}

describe('NewTicketModal — ctrl+t new-ticket dialog', () => {
  it('opens, paints the dialog, Esc dismisses and restores focus', async () => {
    const { stores, enter } = setup();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    await tick();
    expect(lastFrame()).not.toContain('New Ticket');

    enter();
    await tick();
    expect(lastFrame()).toContain('New Ticket');
    expect(selectActiveMode(stores.modes)?.id).toBe(NEW_TICKET_MODE_ID);

    stdin.write(ESC);
    await tick();
    expect(selectActiveMode(stores.modes)).toBeNull();
    expect(lastFrame()).not.toContain('New Ticket');
    expect(stores.focus.getState().intendedId).toBe('tickets'); // prior focus restored
  });

  it('accepts printable char input, renders updated title value', async () => {
    const { stores, enter } = setup();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();

    stdin.write('f');
    stdin.write('i');
    stdin.write('x');
    await tick();
    expect(lastFrame()).toContain('fix');
  });

  it('backspace deletes last char', async () => {
    const { stores, enter } = setup();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();

    stdin.write('a');
    stdin.write('b');
    await tick();
    expect(lastFrame()).toContain('ab');

    stdin.write('\x7f'); // backspace
    await tick();
    expect(lastFrame()).toContain('a');
  });

  it('submit fires ticket.quick_create RPC and dismisses, restoring focus', async () => {
    const { stores, bus, enter } = setup();
    const onSubmit = vi.fn();
    enter({ onSubmit });

    const { stdin } = render(<Harness stores={stores} />);
    await tick();

    // Type a title.
    stdin.write('f');
    stdin.write('i');
    stdin.write('x');
    stdin.write(' ');
    stdin.write('b');
    stdin.write('u');
    stdin.write('g');
    await tick();

    // Press Enter to submit.
    stdin.write('\r');
    await tick();

    expect(selectActiveMode(stores.modes)).toBeNull(); // modal dismissed
    expect(stores.focus.getState().intendedId).toBe('tickets'); // focus restored

    // Allow the async RPC to settle.
    await tick();
    expect(bus.rpcCalls.length).toBe(1);
    expect(bus.rpcCalls[0]).toMatchObject({
      method: 'ticket.quick_create',
      params: { title: 'fix bug' },
    });
    await tick();
    expect(onSubmit).toHaveBeenCalledWith('t-001', 'my ticket');
  });

  it('submit with empty title shows an error and does NOT fire RPC', async () => {
    const { stores, bus, enter } = setup();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();

    stdin.write('\r'); // Enter with empty title.
    await tick();
    expect(lastFrame()).toContain('Ticket title is required');
    expect(selectActiveMode(stores.modes)?.id).toBe(NEW_TICKET_MODE_ID); // still up
    expect(bus.rpcCalls.length).toBe(0);
  });

  it('captures exclusively: ctrl+f does NOT focus chat while the modal is up', async () => {
    const { stores, enter } = setup();
    const { stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();

    // ctrl+f (\x06) would normally focus chat; must be swallowed under the modal.
    stdin.write('\x06');
    await tick();
    expect(stores.focus.getState().intendedId).toBe('tickets'); // focus unmoved
    expect(selectActiveMode(stores.modes)?.id).toBe(NEW_TICKET_MODE_ID); // modal still up
  });

  it('ctrl+u clears the field', async () => {
    const { stores, enter } = setup();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();

    stdin.write('a');
    stdin.write('b');
    stdin.write('c');
    await tick();
    expect(lastFrame()).toContain('abc');

    stdin.write('\x15'); // ctrl+u
    await tick();
    expect(lastFrame()).not.toContain('abc');
    // The placeholder should appear again.
    expect(lastFrame()).toContain('Short description');
  });

  it('dismiss callback fires on Esc', async () => {
    const { stores, enter } = setup();
    const onDismiss = vi.fn();
    enter({ onDismiss });

    const { stdin } = render(<Harness stores={stores} />);
    await tick();
    stdin.write(ESC);
    await tick();
    expect(onDismiss).toHaveBeenCalledOnce();
  });
});
