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
import { beforeEach, describe, expect, it, vi } from 'vitest';
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
import { createSpawnActions } from '../../src/store/dialogs/spawnActions.js';
import { selectLiveToasts, toastStore } from '../../src/store/toast/toastStore.js';

const ESC = '\x1b';

/** Let Ink flush a render + post-render effects. */
async function tick(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 20));
}

/** The `kind`s of every `command.submit` call, in order — spawn (+ optional kickoff agent.message). */
function submitKinds(bus: FakeBusClient): string[] {
  return bus.rpcCalls
    .filter((c) => c.method === 'command.submit')
    .map((c) => String((c.params as { kind: string }).kind));
}

/** The payload of the `crow.spawn_rogue` command submit (the spawn params crossing the wire). */
function spawnSubmitPayload(bus: FakeBusClient): Record<string, unknown> {
  const call = bus.rpcCalls.find(
    (c) =>
      c.method === 'command.submit' && (c.params as { kind: string }).kind === 'crow.spawn_rogue',
  );
  return (call?.params as { payload: Record<string, unknown> }).payload;
}

/** The payload of the kickoff `agent.message` command submit, if one was sent. */
function kickoffSubmitPayload(bus: FakeBusClient): Record<string, unknown> | undefined {
  const call = bus.rpcCalls.find(
    (c) => c.method === 'command.submit' && (c.params as { kind: string }).kind === 'agent.message',
  );
  return call ? (call.params as { payload: Record<string, unknown> }).payload : undefined;
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
  // F2: `crow.spawn_rogue` is an orchestrator command kind routed through `command.submit` +
  // `command.status`. The submit returns the spawned `agent_id` (in `result_json`); the kickoff
  // message, when present, is delivered as a separate `agent.message` command (also via submit).
  bus.stubRpc('command.submit', { ok: true, command_id: 'cmd-1' });
  bus.stubRpc('command.status', {
    ok: true,
    status: 'done',
    result_json: JSON.stringify({ handled: true, agent_id: 'rogue-001' }),
  });
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

/** All live error toasts on the singleton at the current instant. */
function errorToasts() {
  const live = selectLiveToasts(toastStore.getState().toasts, Date.now());
  return live.filter((t) => t.severity === 'error');
}

describe('SpawnWizardModal — ctrl+s spawn wizard', () => {
  // The toast singleton is shared global state; reset it between cases (toastStore's own idiom).
  beforeEach(() => {
    toastStore.getState().clear();
  });

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

    await tick(); // let the async command (submit → poll → resolve) settle
    await tick();
    const spawnPayload = spawnSubmitPayload(bus);
    // The spawn command carries the required harness + model + the chosen effort.
    expect(spawnPayload).toMatchObject({ harness: 'claude', model: 'sonnet', effort: 'medium' });
    // No kickoff: only the spawn command was submitted (no follow-up agent.message).
    expect(submitKinds(bus)).toEqual(['crow.spawn_rogue']);
    await tick();
    expect(onSubmit).toHaveBeenCalledWith('medium', null);
  });

  it('successful submit pushes NO error toast', async () => {
    const { stores, enter } = setup(null); // setup() stubs the command to resolve
    enter();
    const { stdin } = render(<Harness stores={stores} />);
    await tick();
    stdin.write('\r'); // confirm effort → submit (no context)
    await tick();
    await tick();
    await tick();
    expect(errorToasts()).toHaveLength(0);
  });

  it('a rejected spawn pushes an error toast with the rejection message', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const bus = new FakeBusClient();
    // `crow.spawn_rogue` routes through `command.submit`; reject at the submit choke point so
    // `spawnRogue` rejects. Exit-then-act: the wizard is gone before this lands; the toast must
    // still fire on the global singleton with the structured UdsBusClient text.
    bus.stubRpc('command.submit', () => {
      throw new Error('rpc error [internal]: spawn failed');
    });
    stores.modes
      .getState()
      .enter(spawnWizardMode(stores.modes, createSpawnActions(bus), { spawnContext: null }));
    const { stdin } = render(<Harness stores={stores} />);
    await tick();

    stdin.write('\r'); // confirm effort → submit (no context)
    await tick();

    expect(selectActiveMode(stores.modes)).toBeNull(); // dismissed (exit-then-act)
    await tick();
    await tick();

    const errs = errorToasts();
    expect(errs).toHaveLength(1);
    expect(errs[0]?.text).toBe('rpc error [internal]: spawn failed');
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
    await tick();
    expect(spawnSubmitPayload(bus)).toMatchObject({ effort: 'low' }); // first option (cursor 0)
    // Reference-by-path: the kickoff is delivered as a separate agent.message to the spawned rogue.
    expect(submitKinds(bus)).toEqual(['crow.spawn_rogue', 'agent.message']);
    expect(kickoffSubmitPayload(bus)).toMatchObject({
      agent_id: 'rogue-001',
      message: `Please read ${TEST_CONTEXT.path} before starting.`,
    });
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
    await tick();
    const kickoff = kickoffSubmitPayload(bus);
    expect(typeof kickoff?.['message']).toBe('string');
    expect(String(kickoff?.['message'])).toContain('.murder/notes/my-note.md');
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
    await tick();
    expect(spawnSubmitPayload(bus)).toMatchObject({ effort: 'low' });
    // Declined context → no kickoff agent.message, only the spawn command.
    expect(submitKinds(bus)).toEqual(['crow.spawn_rogue']);
    expect(kickoffSubmitPayload(bus)).toBeUndefined();
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

/**
 * H4 (F11 guard) — spawn-wizard payload contract.
 *
 * Pins the REAL `createSpawnActions(...).spawnRogue(...)` payload to the live handler's required
 * schema so the F2 fix can never silently regress once Textual is gone. The required field set
 * below is anchored to `Orchestrator.spawn_rogue_command` (murder/runtime/orchestration/
 * orchestrator.py:~564), which requires non-empty `harness` and a `model` string; the Python side
 * is pinned by tests/unit/test_spawn_effort_bus.py (the `rejects_missing_*` cases). A change to the
 * handler's required fields should surface as a failure on BOTH sides.
 *
 * The regression this guards: the old wizard sent `{effort}` / `{effort, kickoff_message}` — it
 * dropped harness/model and inlined a kickoff field the handler ignores. So we assert (1) the
 * required fields are always present and truthy, (2) `kickoff_message` is NEVER inlined into the
 * spawn payload, and (3) a supplied kickoff is delivered out-of-band as `agent.message` (and is not
 * silently dropped), while an empty/absent kickoff fires no follow-up command.
 */
describe('H4 — spawn payload contract (real spawn action)', () => {
  /** REQUIRED fields of the live `crow.spawn_rogue` handler — see spawn_rogue_command (orchestrator.py:~564). */
  const REQUIRED_SPAWN_FIELDS = ['harness', 'model'] as const;

  /** A bus stubbed exactly like setup(): submit accepted, status resolves with a spawned agent_id. */
  function liveStubBus(): FakeBusClient {
    const bus = new FakeBusClient();
    bus.stubRpc('command.submit', { ok: true, command_id: 'cmd-1' });
    bus.stubRpc('command.status', {
      ok: true,
      status: 'done',
      result_json: JSON.stringify({ handled: true, agent_id: 'rogue-001' }),
    });
    return bus;
  }

  it('always sends every required field (truthy) and never inlines kickoff_message', async () => {
    const bus = liveStubBus();
    await createSpawnActions(bus).spawnRogue({
      harness: 'claude',
      model: 'sonnet',
      effort: 'medium',
    });

    const payload = spawnSubmitPayload(bus);
    // Iterating the required set so a dropped field names itself in the failure.
    for (const field of REQUIRED_SPAWN_FIELDS) {
      expect(payload[field], `spawn payload missing required field "${field}"`).toBeTruthy();
    }
    // Regression guard: kickoff must NOT be re-inlined into the spawn payload (the live handler
    // ignores it — it rides out-of-band as agent.message).
    expect(payload).not.toHaveProperty('kickoff_message');
  });

  it('delivers a supplied kickoff out-of-band as agent.message (not silently dropped)', async () => {
    const bus = liveStubBus();
    await createSpawnActions(bus).spawnRogue({
      harness: 'claude',
      model: 'sonnet',
      kickoffMessage: 'Please read .murder/notes/x.md before starting.',
    });

    // Spawn first, then the kickoff as a separate command — kickoff is not lost.
    expect(submitKinds(bus)).toEqual(['crow.spawn_rogue', 'agent.message']);
    expect(kickoffSubmitPayload(bus)).toMatchObject({
      agent_id: 'rogue-001',
      message: 'Please read .murder/notes/x.md before starting.',
    });
  });

  it('fires no follow-up command when kickoff is empty or absent', async () => {
    const busEmpty = liveStubBus();
    await createSpawnActions(busEmpty).spawnRogue({
      harness: 'claude',
      model: 'sonnet',
      kickoffMessage: '',
    });
    expect(submitKinds(busEmpty)).toEqual(['crow.spawn_rogue']);
    expect(kickoffSubmitPayload(busEmpty)).toBeUndefined();

    const busAbsent = liveStubBus();
    await createSpawnActions(busAbsent).spawnRogue({ harness: 'claude', model: 'sonnet' });
    expect(submitKinds(busAbsent)).toEqual(['crow.spawn_rogue']);
    expect(kickoffSubmitPayload(busAbsent)).toBeUndefined();
  });
});

describe('ctrl+s dispatcher test', () => {
  it('ctrl+s fires the spawn handler when CHAT is focused (C11 dual-purpose chord)', async () => {
    // C11: ctrl+s spawns ONLY when chat is focused; from a panel it stars the highlighted row.
    const stores = createInputStores([], 'chat');
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
    bus.stubRpc('command.submit', { ok: true, command_id: 'cmd-1' });
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
