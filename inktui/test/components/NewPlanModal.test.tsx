/**
 * NewPlanModal tests — verifies the `alt+p` new-plan modal mode against the C7M idiom.
 *
 * Copy recipe (mirrors ConfirmModal.test.tsx):
 *  1. Build input stores, enter the mode imperatively.
 *  2. Drive with simulated keys: open → assert paint → type → submit → assert RPC fired + focus
 *     restored.
 *  3. Esc dismisses without firing RPC.
 *  4. Panel chord does NOT fire while the modal is up (exclusive capture).
 *  5. Pure dispatcher test: alt+p fires `newPlan` handler, alt+t fires `newTicket` handler.
 */

import { render } from 'ink-testing-library';
import type { JSX } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { NEW_PLAN_MODE_ID, newPlanMode } from '../../src/components/NewPlanModal.js';
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
function RootInput({
  newPlan,
  newTicket,
}: {
  readonly newPlan?: () => void;
  readonly newTicket?: () => void;
}): null {
  // Build deferred handlers, only passing keys that have real values (exactOptionalPropertyTypes).
  const deferred = {
    ...(newPlan !== undefined ? { newPlan } : {}),
    ...(newTicket !== undefined ? { newTicket } : {}),
  };
  useRootInput(deferred);
  return null;
}

/** The harness: the overlay + root loop inside the providers. */
function Harness({
  stores,
  newPlan,
  newTicket,
}: {
  readonly stores: ReturnType<typeof createInputStores>;
  readonly newPlan?: () => void;
  readonly newTicket?: () => void;
}): JSX.Element {
  // Only pass defined handlers to avoid exactOptionalPropertyTypes violations.
  const rootProps = {
    ...(newPlan !== undefined ? { newPlan } : {}),
    ...(newTicket !== undefined ? { newTicket } : {}),
  };
  return (
    <InputStoresProvider value={stores}>
      <RootInput {...rootProps} />
      <Overlay />
    </InputStoresProvider>
  );
}

/** Build stores with tickets panel focused (the prior focus to restore). */
function setup() {
  const stores = createInputStores(['tickets'], 'tickets');
  const bus = new FakeBusClient();
  const actions = createDialogActions(bus);

  bus.stubRpc('plan.create', { handled: true, plan_name: 'test', agent_id: 'agent-1' });

  const enter = () => stores.modes.getState().enter(newPlanMode(stores.modes, actions, {}));
  return { stores, bus, actions, enter };
}

/** All live error toasts on the singleton at the current instant. */
function errorToasts() {
  const live = selectLiveToasts(toastStore.getState().toasts, Date.now());
  return live.filter((t) => t.severity === 'error');
}

describe('NewPlanModal — alt+p new-plan dialog', () => {
  // The toast singleton is shared global state; reset it between cases (toastStore's own idiom).
  beforeEach(() => {
    toastStore.getState().clear();
  });

  it('opens, paints the dialog, captures input, dismisses on Esc, and restores focus', async () => {
    const { stores, enter } = setup();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    await tick();
    expect(lastFrame()).not.toContain('New Plan');

    enter();
    await tick();
    expect(lastFrame()).toContain('New Plan');
    expect(selectActiveMode(stores.modes)?.id).toBe(NEW_PLAN_MODE_ID);

    // Esc dismisses.
    stdin.write(ESC);
    await tick();
    expect(selectActiveMode(stores.modes)).toBeNull();
    expect(lastFrame()).not.toContain('New Plan');
    expect(stores.focus.getState().intendedId).toBe('tickets'); // prior focus restored
  });

  it('accepts printable char input via onUncaptured, renders updated value', async () => {
    const { stores, enter } = setup();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();
    expect(lastFrame()).toContain('New Plan');

    // Type plan name chars.
    stdin.write('m');
    stdin.write('y');
    stdin.write('-');
    stdin.write('p');
    stdin.write('l');
    stdin.write('a');
    stdin.write('n');
    await tick();
    expect(lastFrame()).toContain('my-plan');
  });

  it('backspace deletes the last char', async () => {
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
    // After backspace: only 'a' in the plan name field, not 'ab' as an input value.
    // The frame contains 'a█' (cursor after 'a') but not 'ab█'.
    expect(lastFrame()).not.toContain('ab█');
  });

  it('submit fires the plan.create RPC and dismisses the modal', async () => {
    const { stores, bus } = setup();
    const onSubmit = vi.fn();
    stores.modes
      .getState()
      .enter(newPlanMode(stores.modes, createDialogActions(bus), { onSubmit }));
    const { stdin } = render(<Harness stores={stores} />);
    await tick();

    // Type a plan name.
    stdin.write('m');
    stdin.write('y');
    stdin.write('-');
    stdin.write('p');
    stdin.write('l');
    stdin.write('a');
    stdin.write('n');
    await tick();

    // Press Enter to submit.
    stdin.write('\r');
    await tick();

    expect(selectActiveMode(stores.modes)).toBeNull(); // modal dismissed
    expect(stores.focus.getState().intendedId).toBe('tickets'); // focus restored

    // Give the async RPC a tick to complete.
    await tick();
    expect(bus.rpcCalls.length).toBe(1);
    expect(bus.rpcCalls[0]).toMatchObject({
      method: 'plan.create',
      params: { plan_name: 'my-plan', message: '' },
    });
    await tick();
    expect(onSubmit).toHaveBeenCalledWith('my-plan', '');
  });

  it('successful submit pushes NO error toast', async () => {
    const { stores } = setup(); // setup() stubs plan.create to resolve
    const { stdin } = render(<Harness stores={stores} />);
    await tick();
    for (const ch of 'my-plan') stdin.write(ch);
    await tick();
    stdin.write('\r');
    await tick();
    await tick();
    expect(errorToasts()).toHaveLength(0);
  });

  it('a rejected plan.create pushes an error toast with the rejection message', async () => {
    const { stores } = setup();
    const bus = new FakeBusClient();
    // Exit-then-act: the modal is gone before this rejects; the toast must still fire on the
    // global singleton (not tied to the unmounted modal). Use the structured UdsBusClient text.
    bus.stubRpc('plan.create', () => {
      throw new Error('rpc error [internal]: plan create failed');
    });
    stores.modes.getState().enter(newPlanMode(stores.modes, createDialogActions(bus), {}));
    const { stdin } = render(<Harness stores={stores} />);
    await tick();

    for (const ch of 'my-plan') stdin.write(ch);
    await tick();
    stdin.write('\r'); // submit
    await tick();

    // Modal dismissed (exit-then-act), then the rejection lands.
    expect(selectActiveMode(stores.modes)).toBeNull();
    await tick();

    const errs = errorToasts();
    expect(errs).toHaveLength(1);
    expect(errs[0]?.text).toBe('rpc error [internal]: plan create failed');
  });

  it('submit with empty plan name shows an error and does NOT fire RPC', async () => {
    const { stores, bus, enter } = setup();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();

    // Press Enter with empty name.
    stdin.write('\r');
    await tick();
    expect(lastFrame()).toContain('Plan name is required');
    expect(selectActiveMode(stores.modes)?.id).toBe(NEW_PLAN_MODE_ID); // still up
    expect(bus.rpcCalls.length).toBe(0);
    // Field validation stays INLINE (modal open) — it must NOT also fire an error toast.
    expect(errorToasts()).toHaveLength(0);
  });

  it('captures exclusively: ctrl+1 does NOT toggle a panel while the modal is up', async () => {
    const { stores, enter } = setup();
    const { stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();

    // ctrl+1 (\x01) would normally toggle the plans panel; must not while modal is up.
    stdin.write('\x01');
    await tick();
    expect(selectActiveMode(stores.modes)?.id).toBe(NEW_PLAN_MODE_ID); // modal still up
    // plans panel should NOT have been toggled on (it was not visible before).
    expect(stores.panels.getState().visible.has('plans')).toBe(false);
  });

  it('tab cycles the active field', async () => {
    const { stores, enter } = setup();
    const { stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();

    // Initially focused on plan name; type something, tab to message field.
    stdin.write('m');
    stdin.write('y');
    await tick();
    stdin.write('\t'); // tab to message field
    await tick();
    stdin.write('h'); // type in message field
    stdin.write('i');
    await tick();

    // The modal should still be up and contain the message.
    expect(selectActiveMode(stores.modes)?.id).toBe(NEW_PLAN_MODE_ID);
  });
});

describe('global chords — alt+p and alt+t', () => {
  it('alt+p fires the newPlan handler', async () => {
    const stores = createInputStores(['tickets'], 'tickets');
    const newPlanFn = vi.fn();
    const { stdin } = render(<Harness stores={stores} newPlan={newPlanFn} />);
    await tick();

    // alt+p = \x1bp
    stdin.write('\x1bp');
    await tick();
    expect(newPlanFn).toHaveBeenCalledOnce();
  });

  it('alt+t fires the newTicket handler', async () => {
    const stores = createInputStores(['tickets'], 'tickets');
    const newTicketFn = vi.fn();
    const { stdin } = render(<Harness stores={stores} newTicket={newTicketFn} />);
    await tick();

    // alt+t = \x1bt
    stdin.write('\x1bt');
    await tick();
    expect(newTicketFn).toHaveBeenCalledOnce();
  });

  it('alt+p does NOT fire while the new-plan modal is up (exclusive capture)', async () => {
    const { stores, enter } = setup();
    const newPlanFn = vi.fn();
    const { stdin } = render(<Harness stores={stores} newPlan={newPlanFn} />);
    enter();
    await tick();

    stdin.write('\x1bp'); // alt+p
    await tick();
    // The modal is up (its onUncaptured gets \x1bp with meta=true → returns false → swallowed).
    // newPlanFn must NOT have been called.
    expect(newPlanFn).not.toHaveBeenCalled();
    expect(selectActiveMode(stores.modes)?.id).toBe(NEW_PLAN_MODE_ID);
  });
});
