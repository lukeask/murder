/**
 * NewTicketModal tests — verifies the `alt+t` new-ticket modal mode against the C7M idiom.
 *
 * Copy recipe (mirrors ConfirmModal.test.tsx — identical harness structure):
 *  1. Build input stores with a panel focused, enter the mode imperatively.
 *  2. Open → assert paint → type → submit → assert `ticket.quick_create` RPC fired → focus restored.
 *  3. Esc dismisses without firing RPC.
 *  4. Panel chord does NOT fire while the modal is up (exclusive capture).
 */

import { render } from 'ink-testing-library';
import type { JSX } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { NEW_TICKET_MODE_ID, newTicketMode } from '../../src/components/NewTicketModal.js';
import { Overlay } from '../../src/components/Overlay.js';
import { InputStoresProvider } from '../../src/hooks/useInputStores.js';
import { useRootInput } from '../../src/hooks/useRootInput.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import { selectActiveMode } from '../../src/input/modeStore.js';
import { createDialogActions } from '../../src/store/dialogs/dialogActions.js';
import { selectLiveToasts, toastStore } from '../../src/store/toast/toastStore.js';

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

  // F2: `ticket.quick_create` is an orchestrator command kind routed through `command.submit` +
  // `command.status` (not a standalone RPC). Status returns `'done'` on the first poll with the
  // worker reply JSON-encoded in `result_json` (matching the live `command.status` shape).
  bus.stubRpc('command.submit', { ok: true, command_id: 'cmd-1' });
  bus.stubRpc('command.status', {
    ok: true,
    status: 'done',
    result_json: JSON.stringify({ handled: true, ticket_id: 't-001', title: 'my ticket' }),
  });

  const actions = createDialogActions(bus);
  const enter = (opts = {}) =>
    stores.modes.getState().enter(newTicketMode(stores.modes, actions, opts));
  return { stores, bus, actions, enter };
}

/** All live error toasts on the singleton at the current instant. */
function errorToasts() {
  const live = selectLiveToasts(toastStore.getState().toasts, Date.now());
  return live.filter((t) => t.severity === 'error');
}

describe('NewTicketModal — alt+t new-ticket dialog', () => {
  // The toast singleton is shared global state; reset it between cases (toastStore's own idiom).
  beforeEach(() => {
    toastStore.getState().clear();
  });

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

    // Allow the async command (submit → poll → resolve) to settle.
    await tick();
    await tick();
    // The submit carries the `ticket.quick_create` command kind + the title payload.
    const submitCall = bus.rpcCalls.find((c) => c.method === 'command.submit');
    expect(submitCall?.params).toMatchObject({
      kind: 'ticket.quick_create',
      payload: { title: 'fix bug' },
    });
    await tick();
    expect(onSubmit).toHaveBeenCalledWith('t-001', 'my ticket');
  });

  it('successful submit pushes NO error toast', async () => {
    const { stores, enter } = setup(); // setup() stubs the command to resolve
    enter();
    const { stdin } = render(<Harness stores={stores} />);
    await tick();
    for (const ch of 'fix bug') stdin.write(ch);
    await tick();
    stdin.write('\r');
    await tick();
    await tick();
    await tick();
    expect(errorToasts()).toHaveLength(0);
  });

  it('a rejected ticket create pushes an error toast with the rejection message', async () => {
    const stores = createInputStores(['tickets'], 'tickets');
    const bus = new FakeBusClient();
    // `ticket.quick_create` routes through `command.submit`; reject at the submit choke point so
    // `quickCreateTicket` rejects. Exit-then-act: the modal is gone before this lands; the toast
    // must still fire on the global singleton with the structured UdsBusClient text.
    bus.stubRpc('command.submit', () => {
      throw new Error('rpc error [internal]: ticket create failed');
    });
    stores.modes.getState().enter(newTicketMode(stores.modes, createDialogActions(bus), {}));
    const { stdin } = render(<Harness stores={stores} />);
    await tick();

    for (const ch of 'fix bug') stdin.write(ch);
    await tick();
    stdin.write('\r'); // submit
    await tick();

    expect(selectActiveMode(stores.modes)).toBeNull(); // dismissed (exit-then-act)
    await tick();
    await tick();

    const errs = errorToasts();
    expect(errs).toHaveLength(1);
    expect(errs[0]?.text).toBe('rpc error [internal]: ticket create failed');
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
    // Field validation stays INLINE (modal open) — it must NOT also fire an error toast.
    expect(errorToasts()).toHaveLength(0);
  });

  it('captures exclusively: alt+f does NOT focus chat while the modal is up', async () => {
    const { stores, enter } = setup();
    const { stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();

    // alt+f (\x1bf) would normally focus chat; must be swallowed under the modal.
    stdin.write('\x1bf');
    await tick();
    expect(stores.focus.getState().intendedId).toBe('tickets'); // focus unmoved
    expect(selectActiveMode(stores.modes)?.id).toBe(NEW_TICKET_MODE_ID); // modal still up
  });

  it('alt+u clears the field', async () => {
    const { stores, enter } = setup();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();

    stdin.write('a');
    stdin.write('b');
    stdin.write('c');
    await tick();
    expect(lastFrame()).toContain('abc');

    stdin.write('\x1bu'); // alt+u
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
