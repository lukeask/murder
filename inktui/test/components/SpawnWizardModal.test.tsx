/**
 * SpawnWizardModal tests — verifies the `ctrl+s` spawn wizard against the C7M idiom.
 *
 * Test coverage:
 *  1. Opens, paints the wizard, Esc dismisses and restores focus.
 *  2. Effort step: j/k navigation, Enter confirms.
 *  3. Context step: shown when spawnContext is non-null; y/enter = accept; n = decline.
 *  4. Submit (no context) fires `crow.spawn_rogue` with effort only.
 *  5. Submit (context, accepted) fires with effort + reference-by-path kickoff_message.
 *  6. Submit (context, declined) fires with effort only (no kickoff_message).
 *  7. Panel chord does NOT fire while the wizard is up (exclusive capture).
 *  8. Pure dispatcher test: ctrl+s fires the `spawn` handler.
 */

import { render } from 'ink-testing-library';
import type { JSX } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { Overlay } from '../../src/components/Overlay.js';
import {
  DEFAULT_EFFORT_OPTIONS,
  SPAWN_WIZARD_MODE_ID,
  type SpawnContext,
  spawnWizardMode,
} from '../../src/components/SpawnWizardModal.js';
import { InputStoresProvider } from '../../src/hooks/useInputStores.js';
import { useRootInput } from '../../src/hooks/useRootInput.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import { selectActiveMode } from '../../src/input/modeStore.js';
import { createSpawnActions, type SpawnRogueParams } from '../../src/store/dialogs/spawnActions.js';

const ESC = '\x1b';

/** Let Ink flush a render + post-render effects. */
async function tick(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 20));
}

/** Runs the root input loop inside the providers, with an optional spawn handler override. */
function RootInput({ spawn }: { readonly spawn?: () => void }): null {
  // Build deferred handlers, only passing keys with real values (exactOptionalPropertyTypes).
  const deferred = { ...(spawn !== undefined ? { spawn } : {}) };
  useRootInput(deferred);
  return null;
}

/** The test harness: overlay + root loop inside the providers. */
function Harness({
  stores,
  spawn,
}: {
  readonly stores: ReturnType<typeof createInputStores>;
  readonly spawn?: () => void;
}): JSX.Element {
  const rootProps = { ...(spawn !== undefined ? { spawn } : {}) };
  return (
    <InputStoresProvider value={stores}>
      <RootInput {...rootProps} />
      <Overlay />
    </InputStoresProvider>
  );
}

/** Build stores with the notes panel focused (prior focus to restore on dismiss). */
function setup(spawnContext: SpawnContext | null = null) {
  const stores = createInputStores(['notes'], 'notes');
  const bus = new FakeBusClient();
  bus.stubRpc('crow.spawn_rogue', { handled: true, agent_id: 'rogue-001' });
  const actions = createSpawnActions(bus);
  const enter = (opts: Parameters<typeof spawnWizardMode>[2] = {}) =>
    stores.modes
      .getState()
      .enter(spawnWizardMode(stores.modes, actions, { spawnContext, ...opts }));
  return { stores, bus, actions, enter };
}

const TEST_CONTEXT: SpawnContext = {
  title: 'my-note',
  path: '.murder/notes/my-note.md',
};

describe('SpawnWizardModal — ctrl+s spawn wizard', () => {
  it('opens, paints the wizard title, Esc dismisses and restores focus', async () => {
    const { stores, enter } = setup();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    await tick();
    expect(lastFrame()).not.toContain('Spawn Rogue');

    enter();
    await tick();
    expect(lastFrame()).toContain('Spawn Rogue');
    expect(selectActiveMode(stores.modes)?.id).toBe(SPAWN_WIZARD_MODE_ID);

    stdin.write(ESC);
    await tick();
    expect(selectActiveMode(stores.modes)).toBeNull();
    expect(lastFrame()).not.toContain('Spawn Rogue');
    expect(stores.focus.getState().intendedId).toBe('notes'); // prior focus restored
  });

  it('shows effort options on step 1', async () => {
    const { stores, enter } = setup();
    const { lastFrame } = render(<Harness stores={stores} />);
    enter();
    await tick();
    // All default effort options should appear.
    for (const opt of DEFAULT_EFFORT_OPTIONS) {
      expect(lastFrame()).toContain(opt);
    }
    // Hint text should appear.
    expect(lastFrame()).toContain('j/k');
  });

  it('j/k cursor moves the effort selection', async () => {
    const { stores, enter } = setup();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();
    // Initially 'low' (cursor 0) should be highlighted.
    expect(lastFrame()).toContain('› low');

    // Press j — cursor moves to 'medium'.
    stdin.write('j');
    await tick();
    expect(lastFrame()).toContain('› medium');

    // Press j again — cursor moves to 'high'.
    stdin.write('j');
    await tick();
    expect(lastFrame()).toContain('› high');

    // Press k — back to 'medium'.
    stdin.write('k');
    await tick();
    expect(lastFrame()).toContain('› medium');
  });

  it('submit (no context) fires crow.spawn_rogue with effort only', async () => {
    const { stores, bus, enter } = setup(null);
    const onSubmit = vi.fn();
    enter({ onSubmit });

    const { stdin } = render(<Harness stores={stores} />);
    await tick();

    // Navigate to 'medium' and confirm.
    stdin.write('j');
    await tick();
    stdin.write('\r'); // Enter on effort step — no context, goes straight to submit.
    await tick();

    expect(selectActiveMode(stores.modes)).toBeNull(); // wizard dismissed
    expect(stores.focus.getState().intendedId).toBe('notes'); // focus restored

    await tick(); // let the async RPC settle
    expect(bus.rpcCalls.length).toBe(1);
    expect(bus.rpcCalls[0]).toMatchObject({
      method: 'crow.spawn_rogue',
      params: { effort: 'medium' },
    });
    // kickoff_message must NOT be present when no context.
    const params0 = bus.rpcCalls[0]?.params as SpawnRogueParams;
    expect(params0.kickoff_message).toBeUndefined();
    await tick();
    expect(onSubmit).toHaveBeenCalledWith('medium', null);
  });

  it('shows context step after effort when spawnContext is provided', async () => {
    const { stores, enter } = setup(TEST_CONTEXT);
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();

    // Confirm effort step with Enter — should advance to context step.
    stdin.write('\r');
    await tick();
    // Context step should now be visible.
    expect(lastFrame()).toContain('my-note');
    expect(lastFrame()).toContain('[yes]'); // default is yes
    expect(lastFrame()).toContain('y/enter');
  });

  it('context step Enter (default yes) fires with reference-by-path kickoff_message', async () => {
    const { stores, bus, enter } = setup(TEST_CONTEXT);
    const onSubmit = vi.fn();
    enter({ onSubmit });

    const { stdin } = render(<Harness stores={stores} />);
    await tick();

    stdin.write('\r'); // confirm effort
    await tick();
    stdin.write('\r'); // confirm context step (default = yes)
    await tick();

    expect(selectActiveMode(stores.modes)).toBeNull();
    await tick();
    expect(bus.rpcCalls.length).toBe(1);
    const params = bus.rpcCalls[0]?.params as SpawnRogueParams;
    expect(params.effort).toBe('low'); // first option (cursor 0)
    // Reference-by-path: kickoff_message tells rogue to READ the path.
    expect(params.kickoff_message).toBe(`Please read ${TEST_CONTEXT.path} before starting.`);
    await tick();
    expect(onSubmit).toHaveBeenCalledWith(
      'low',
      `Please read ${TEST_CONTEXT.path} before starting.`,
    );
  });

  it('context step y fires with kickoff_message', async () => {
    const { stores, bus, enter } = setup(TEST_CONTEXT);
    enter();
    const { stdin } = render(<Harness stores={stores} />);
    await tick();

    stdin.write('\r'); // confirm effort
    await tick();
    stdin.write('y'); // explicitly accept context
    await tick();

    expect(selectActiveMode(stores.modes)).toBeNull();
    await tick();
    const params = bus.rpcCalls[0]?.params as SpawnRogueParams;
    expect(typeof params.kickoff_message).toBe('string');
    expect(params.kickoff_message).toContain('.murder/notes/my-note.md');
  });

  it('context step n declines context — fires without kickoff_message', async () => {
    const { stores, bus, enter } = setup(TEST_CONTEXT);
    const onSubmit = vi.fn();
    enter({ onSubmit });

    const { stdin } = render(<Harness stores={stores} />);
    await tick();

    stdin.write('\r'); // confirm effort
    await tick();
    stdin.write('n'); // decline context
    await tick();

    expect(selectActiveMode(stores.modes)).toBeNull();
    await tick();
    expect(bus.rpcCalls.length).toBe(1);
    const params = bus.rpcCalls[0]?.params as SpawnRogueParams;
    expect(params.effort).toBe('low');
    expect(params.kickoff_message).toBeUndefined();
    await tick();
    expect(onSubmit).toHaveBeenCalledWith('low', null);
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
    expect(selectActiveMode(stores.modes)).toBeNull();
  });

  it('captures exclusively: ctrl+1 does NOT toggle plans while the wizard is up', async () => {
    const { stores, enter } = setup();
    const { stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();

    // ctrl+1 = \x01 — would normally toggle plans panel.
    stdin.write('\x01');
    await tick();
    expect(selectActiveMode(stores.modes)?.id).toBe(SPAWN_WIZARD_MODE_ID); // wizard still up
    expect(stores.panels.getState().visible.has('plans')).toBe(false); // no panel toggled
  });

  it('shows step counter: 1/1 without context, 1/2 and 2/2 with context', async () => {
    // Without context.
    const { stores: stores1, enter: enter1 } = setup(null);
    const { lastFrame: frame1 } = render(<Harness stores={stores1} />);
    enter1();
    await tick();
    expect(frame1()).toContain('1/1');

    // With context — step 1.
    const { stores: stores2, enter: enter2 } = setup(TEST_CONTEXT);
    const { lastFrame: frame2, stdin: stdin2 } = render(<Harness stores={stores2} />);
    enter2();
    await tick();
    expect(frame2()).toContain('1/2');

    // Advance to step 2.
    stdin2.write('\r');
    await tick();
    expect(frame2()).toContain('2/2');
  });
});

describe('ctrl+s dispatcher test', () => {
  it('ctrl+s fires the spawn handler', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const spawnFn = vi.fn();
    const { stdin } = render(<Harness stores={stores} spawn={spawnFn} />);
    await tick();

    // ctrl+s = \x13
    stdin.write('\x13');
    await tick();
    expect(spawnFn).toHaveBeenCalledOnce();
  });

  it('ctrl+s does NOT fire spawn while wizard is already up (exclusive capture)', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const spawnFn = vi.fn();
    const bus = new FakeBusClient();
    bus.stubRpc('crow.spawn_rogue', { handled: true });
    const actions = createSpawnActions(bus);
    stores.modes.getState().enter(spawnWizardMode(stores.modes, actions, { spawnContext: null }));

    const { stdin } = render(<Harness stores={stores} spawn={spawnFn} />);
    await tick();

    stdin.write('\x13'); // ctrl+s
    await tick();
    // The wizard is up; ctrl+s (with ctrl=true) → onUncaptured not defined → swallowed.
    // spawnFn must NOT be called.
    expect(spawnFn).not.toHaveBeenCalled();
    expect(selectActiveMode(stores.modes)?.id).toBe(SPAWN_WIZARD_MODE_ID);
  });
});
