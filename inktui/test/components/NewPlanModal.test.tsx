/**
 * NewPlanModal tests — verifies the `super+p` new-plan single-form wizard (item 3) against the C7M
 * idiom. The form has three focus groups (body → naming radio → custom-name); Enter confirms a group
 * and advances. Drives the real dispatcher (via the root loop) with simulated keys.
 *
 *  1. Open → paint → type body → Enter advances → naming radio nav → submit.
 *  2. Auto path: Enter on the auto radio submits with `auto_name: true`.
 *  3. Custom path: choosing "name it myself" reveals a name input; submit sends `plan_name`.
 *  4. Esc dismisses without firing the RPC + restores focus.
 *  5. Exclusive capture: ctrl+1 does NOT toggle a panel while the modal is up.
 *  6. The `super+p` global chord fires the `newPlan` handler (and not while a modal is up).
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
  cycleChatView,
}: {
  readonly newPlan?: () => void;
  readonly newTicket?: () => void;
  readonly cycleChatView?: () => void;
}): null {
  const deferred = {
    ...(newPlan !== undefined ? { newPlan } : {}),
    ...(newTicket !== undefined ? { newTicket } : {}),
    ...(cycleChatView !== undefined ? { cycleChatView } : {}),
  };
  useRootInput(deferred);
  return null;
}

/** The harness: the overlay + root loop inside the providers. */
function Harness({
  stores,
  newPlan,
  newTicket,
  cycleChatView,
}: {
  readonly stores: ReturnType<typeof createInputStores>;
  readonly newPlan?: () => void;
  readonly newTicket?: () => void;
  readonly cycleChatView?: () => void;
}): JSX.Element {
  const rootProps = {
    ...(newPlan !== undefined ? { newPlan } : {}),
    ...(newTicket !== undefined ? { newTicket } : {}),
    ...(cycleChatView !== undefined ? { cycleChatView } : {}),
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

  bus.stubCommand('plan.create', {
    handled: true,
    ok: true,
    plan_name: 'auto-named',
    agent_id: 'a-1',
  });

  const enter = (onSubmit?: (name: string) => void) =>
    stores.modes
      .getState()
      .enter(newPlanMode(stores.modes, actions, onSubmit !== undefined ? { onSubmit } : {}));
  return { stores, bus, actions, enter };
}

/** All live error toasts on the singleton at the current instant. */
function errorToasts() {
  const live = selectLiveToasts(toastStore.getState().toasts, Date.now());
  return live.filter((t) => t.severity === 'error');
}

describe('NewPlanModal — super+p new-plan wizard', () => {
  beforeEach(() => {
    toastStore.getState().clear();
  });

  it('opens, paints the form, types the body, dismisses on Esc, restores focus', async () => {
    const { stores, enter } = setup();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    await tick();
    expect(lastFrame()).not.toContain('New Plan');

    enter();
    await tick();
    expect(lastFrame()).toContain('New Plan');
    expect(lastFrame()).toContain('Plan body');
    expect(selectActiveMode(stores.modes)?.id).toBe(NEW_PLAN_MODE_ID);

    for (const ch of 'do the thing') stdin.write(ch);
    await tick();
    expect(lastFrame()).toContain('do the thing');

    stdin.write(ESC);
    await tick();
    expect(selectActiveMode(stores.modes)).toBeNull();
    expect(stores.focus.getState().intendedId).toBe('tickets'); // prior focus restored
  });

  it('auto path: body → Enter → Enter (auto radio) submits with auto_name + body + message', async () => {
    const { stores, bus } = setup();
    const onSubmit = vi.fn();
    stores.modes
      .getState()
      .enter(newPlanMode(stores.modes, createDialogActions(bus), { onSubmit }));
    bus.stubCommand('plan.create', { handled: true, ok: true, plan_name: 'auto-named' });
    const { stdin } = render(<Harness stores={stores} />);
    await tick();

    for (const ch of 'plan body text') stdin.write(ch);
    await tick();
    stdin.write('\r'); // advance body → naming (auto preselected)
    await tick();
    stdin.write('\r'); // confirm auto → submit
    await tick();
    await tick();

    expect(bus.commandCalls.length).toBe(1);
    expect(bus.commandCalls[0]).toMatchObject({
      name: 'plan.create',
      params: { auto_name: true, body: 'plan body text', message: 'plan body text' },
    });
    expect(selectActiveMode(stores.modes)).toBeNull();
    expect(onSubmit).toHaveBeenCalledWith('auto-named');
  });

  it('custom path: choosing "name it myself" reveals a name field; submit sends plan_name', async () => {
    const { stores, bus } = setup();
    stores.modes.getState().enter(newPlanMode(stores.modes, createDialogActions(bus), {}));
    bus.stubCommand('plan.create', { handled: true, ok: true, plan_name: 'my-plan' });
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    await tick();

    for (const ch of 'body') stdin.write(ch);
    await tick();
    stdin.write('\r'); // advance to naming
    await tick();
    stdin.write('l'); // move highlight to "name it myself"
    await tick();
    stdin.write('\r'); // confirm custom → advance to name field
    await tick();
    expect(lastFrame()).toContain('Plan name');

    for (const ch of 'my-plan') stdin.write(ch);
    await tick();
    stdin.write('\r'); // submit
    await tick();
    await tick();

    expect(bus.commandCalls.length).toBe(1);
    expect(bus.commandCalls[0]).toMatchObject({
      name: 'plan.create',
      params: { plan_name: 'my-plan', body: 'body', message: 'body' },
    });
    expect(selectActiveMode(stores.modes)).toBeNull();
  });

  it('empty body submits without manufacturing a stock kickoff message', async () => {
    const { stores, bus } = setup();
    stores.modes.getState().enter(newPlanMode(stores.modes, createDialogActions(bus), {}));
    bus.stubCommand('plan.create', { handled: true, ok: true, plan_name: 'x' });
    const { stdin } = render(<Harness stores={stores} />);
    await tick();

    stdin.write('\r'); // body → naming
    await tick();
    stdin.write('\r'); // confirm auto → submit
    await tick();
    await tick();

    expect(bus.commandCalls.length).toBe(1);
    const params = bus.commandCalls[0]?.params as { message?: string };
    expect(params).not.toHaveProperty('message');
  });

  it('a rejected plan.create pushes an error toast and keeps the form up', async () => {
    const { stores } = setup();
    const bus = new FakeBusClient();
    bus.stubCommand('plan.create', () => {
      throw new Error('rpc error [internal]: plan create failed');
    });
    stores.modes.getState().enter(newPlanMode(stores.modes, createDialogActions(bus), {}));
    const { stdin } = render(<Harness stores={stores} />);
    await tick();

    stdin.write('\r'); // body → naming
    await tick();
    stdin.write('\r'); // confirm auto → submit
    await tick();
    await tick();

    const errs = errorToasts();
    expect(errs).toHaveLength(1);
    expect(errs[0]?.text).toBe('rpc error [internal]: plan create failed');
    expect(selectActiveMode(stores.modes)?.id).toBe(NEW_PLAN_MODE_ID); // still up to retry
  });

  it('captures exclusively: ctrl+1 does NOT toggle a panel while the modal is up', async () => {
    const { stores, enter } = setup();
    const { stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();

    stdin.write('\x01'); // ctrl+1 would normally toggle the plans panel
    await tick();
    expect(selectActiveMode(stores.modes)?.id).toBe(NEW_PLAN_MODE_ID);
    expect(stores.panels.getState().visible.has('plans')).toBe(false);
  });
});

describe('global chords — alt+p and alt+t', () => {
  it('alt+p fires the newPlan handler', async () => {
    const stores = createInputStores(['tickets'], 'tickets');
    const newPlanFn = vi.fn();
    const { stdin } = render(<Harness stores={stores} newPlan={newPlanFn} />);
    await tick();

    stdin.write('\x1bp'); // alt+p
    await tick();
    expect(newPlanFn).toHaveBeenCalledOnce();
  });

  it('alt+t fires cycleChatView, NOT newTicket (TUIchat-3 rebind)', async () => {
    const stores = createInputStores(['tickets'], 'tickets');
    const newTicketFn = vi.fn();
    const cycleChatViewFn = vi.fn();
    const { stdin } = render(
      <Harness stores={stores} newTicket={newTicketFn} cycleChatView={cycleChatViewFn} />,
    );
    await tick();

    stdin.write('\x1bt'); // alt+t — now the chat-view cycle, no longer new-ticket
    await tick();
    expect(cycleChatViewFn).toHaveBeenCalledOnce();
    expect(newTicketFn).not.toHaveBeenCalled();
  });

  it('alt+p does NOT fire while the new-plan modal is up (exclusive capture)', async () => {
    const { stores, enter } = setup();
    const newPlanFn = vi.fn();
    const { stdin } = render(<Harness stores={stores} newPlan={newPlanFn} />);
    enter();
    await tick();

    stdin.write('\x1bp'); // alt+p
    await tick();
    expect(newPlanFn).not.toHaveBeenCalled();
    expect(selectActiveMode(stores.modes)?.id).toBe(NEW_PLAN_MODE_ID);
  });
});
