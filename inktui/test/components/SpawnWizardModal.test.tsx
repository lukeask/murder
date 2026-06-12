/**
 * SpawnWizardModal tests — the `alt+s` dependent-field spawn wizard against the C7M idiom.
 *
 * Flow: harness → model → effort → worktree → [branch] → name → [context].
 *
 * Coverage:
 *  1. Opens / paints / Esc dismisses + restores focus.
 *  2. Harness step renders the valid harnesses; default is claude_code (the bug fix).
 *  3. j/k navigation on a list step (now routed via onUncaptured, not the keymap).
 *  4. Full claude_code flow submits with harness=claude_code + chosen model/effort.
 *  5. Switching to antigravity skips model + effort steps.
 *  6. Switching to cursor skips the model step but keeps effort.
 *  7. "+ new worktree" inserts a branch step with non-empty validation; threads worktree_branch.
 *  8. Name step (blank = autogenerate; typed name threads through).
 *  9. Context step appears last when a doc is focused; y/n reference-by-path.
 * 10. Exclusive capture.
 *
 * The H4 payload-contract + alt+s dispatcher suites below are extended from F11.
 */

import { render } from 'ink-testing-library';
import type { JSX } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { Overlay } from '../../src/components/Overlay.js';
import {
  SPAWN_WIZARD_MODE_ID,
  type SpawnContext,
  spawnWizardHints,
  spawnWizardMode,
} from '../../src/components/SpawnWizardModal.js';
import { InputStoresProvider } from '../../src/hooks/useInputStores.js';
import { useRootInput } from '../../src/hooks/useRootInput.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import { selectActiveMode } from '../../src/input/modeStore.js';
import { createHarnessModelsActions } from '../../src/store/dialogs/harnessModelsActions.js';
import { createSpawnActions } from '../../src/store/dialogs/spawnActions.js';
import { createWorktreeOptionsActions } from '../../src/store/dialogs/worktreeOptionsActions.js';
import { createAppStore } from '../../src/store/store.js';
import { selectLiveToasts, toastStore } from '../../src/store/toast/toastStore.js';

const ESC = '\x1b';

async function tick(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 20));
}

function submitKinds(bus: FakeBusClient): string[] {
  return bus.rpcCalls
    .filter((c) => c.method === 'command.submit')
    .map((c) => String((c.params as { kind: string }).kind));
}

function spawnSubmitPayload(bus: FakeBusClient): Record<string, unknown> {
  const call = bus.rpcCalls.find(
    (c) =>
      c.method === 'command.submit' && (c.params as { kind: string }).kind === 'crow.spawn_rogue',
  );
  return (call?.params as { payload: Record<string, unknown> }).payload;
}

function kickoffSubmitPayload(bus: FakeBusClient): Record<string, unknown> | undefined {
  const call = bus.rpcCalls.find(
    (c) => c.method === 'command.submit' && (c.params as { kind: string }).kind === 'agent.message',
  );
  return call ? (call.params as { payload: Record<string, unknown> }).payload : undefined;
}

function RootInput({ spawn }: { readonly spawn?: () => void }): null {
  const deferred = { ...(spawn !== undefined ? { spawn } : {}) };
  useRootInput(deferred);
  return null;
}

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

/** Build stores (notes panel focused) + a wired bus, and an `enter(opts)` that opens the wizard with
 * the live model + worktree actions wired (so the flow matches production). */
function setup(spawnContext: SpawnContext | null = null) {
  const stores = createInputStores(['notes'], 'notes');
  const bus = new FakeBusClient();
  bus.stubRpc('command.submit', { ok: true, command_id: 'cmd-1' });
  bus.stubRpc('command.status', {
    ok: true,
    status: 'done',
    result_json: JSON.stringify({ handled: true, agent_id: 'rogue-001' }),
  });
  // No state.harness_models_snapshot stub → fetch rejects → static fallback (production-realistic
  // until Workstream A lands).
  const actions = createSpawnActions(bus);
  const modelActions = createHarnessModelsActions(bus);
  const worktreeActions = createWorktreeOptionsActions(bus);
  const enter = (opts: Parameters<typeof spawnWizardMode>[2] = {}) =>
    stores.modes.getState().enter(
      spawnWizardMode(stores.modes, actions, {
        spawnContext,
        modelActions,
        worktreeActions,
        ...opts,
      }),
    );
  return { stores, bus, actions, enter };
}

const TEST_CONTEXT: SpawnContext = { title: 'my-note', path: '.murder/notes/my-note.md' };

/** All live error toasts on the singleton at the current instant. */
function errorToasts() {
  const live = selectLiveToasts(toastStore.getState().toasts, Date.now());
  return live.filter((t) => t.severity === 'error');
}

describe('SpawnWizardModal — dependent-field flow', () => {
  // The toast singleton is shared global state; reset it between cases (toastStore's own idiom).
  beforeEach(() => {
    toastStore.getState().clear();
  });

  it('opens, paints, Esc dismisses and restores focus', async () => {
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
    expect(stores.focus.getState().intendedId).toBe('notes');
  });

  it('first step is the harness picker, default highlighted is claude-code', async () => {
    const { stores, enter } = setup();
    const { lastFrame } = render(<Harness stores={stores} />);
    enter();
    await tick();
    expect(lastFrame()).toContain('Select harness');
    expect(lastFrame()).toContain('› claude-code'); // default cursor at index 0
    expect(lastFrame()).toContain('codex');
    expect(lastFrame()).toContain('antigravity');
  });

  it('j/k navigates the harness list (routed via onUncaptured)', async () => {
    const { stores, enter } = setup();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();
    expect(lastFrame()).toContain('› claude-code');
    stdin.write('j');
    await tick();
    expect(lastFrame()).toContain('› codex');
    stdin.write('k');
    await tick();
    expect(lastFrame()).toContain('› claude-code');
  });

  it('full claude_code flow submits harness=claude_code + chosen model + effort', async () => {
    const { stores, bus, enter } = setup(null);
    const onSubmit = vi.fn();
    enter({ onSubmit });
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    await tick();

    stdin.write('\r'); // confirm harness (claude_code)
    await tick();
    expect(lastFrame()).toContain('Select model');
    stdin.write('j'); // model → opus (index 1)
    await tick();
    stdin.write('\r'); // confirm model
    await tick();
    expect(lastFrame()).toContain('Select effort');
    expect(lastFrame()).toContain('› medium'); // default effort cursor seeded at medium
    stdin.write('\r'); // confirm effort (medium)
    await tick();
    expect(lastFrame()).toContain('Select worktree');
    stdin.write('\r'); // confirm worktree (main)
    await tick();
    expect(lastFrame()).toContain('Rogue name');
    stdin.write('\r'); // confirm name (blank = autogenerate) → submit
    await tick();

    expect(selectActiveMode(stores.modes)).toBeNull();
    await tick();
    await tick();
    expect(spawnSubmitPayload(bus)).toMatchObject({
      harness: 'claude_code',
      model: 'opus',
      effort: 'medium',
    });
    // No worktree fields for main checkout; no name (blank).
    expect(spawnSubmitPayload(bus)).not.toHaveProperty('worktree_path');
    expect(spawnSubmitPayload(bus)).not.toHaveProperty('worktree_branch');
    expect(spawnSubmitPayload(bus)).not.toHaveProperty('name');
    expect(submitKinds(bus)).toEqual(['crow.spawn_rogue']);
    await tick();
    expect(onSubmit).toHaveBeenCalledWith('medium', null);
  });

  it('successful submit pushes NO error toast', async () => {
    const { stores, enter } = setup(null); // setup() stubs the command to resolve
    enter();
    const { stdin } = render(<Harness stores={stores} />);
    await tick();
    // Full claude_code flow: harness → model → effort → worktree → name → submit.
    stdin.write('\r'); // harness (claude_code)
    await tick();
    stdin.write('\r'); // model
    await tick();
    stdin.write('\r'); // effort
    await tick();
    stdin.write('\r'); // worktree (main)
    await tick();
    stdin.write('\r'); // name (blank) → submit
    await tick();
    await tick();
    await tick();
    expect(errorToasts()).toHaveLength(0);
  });

  it('a rejected spawn pushes an error toast with the rejection message', async () => {
    // `crow.spawn_rogue` routes through `command.submit`; reject at the submit choke point so
    // `spawnRogue` rejects. Exit-then-act: the wizard is gone before this lands; the toast must
    // still fire on the global singleton with the structured UdsBusClient text.
    const stores = createInputStores(['notes'], 'notes');
    const bus = new FakeBusClient();
    bus.stubRpc('command.submit', () => {
      throw new Error('rpc error [internal]: spawn failed');
    });
    stores.modes.getState().enter(
      spawnWizardMode(stores.modes, createSpawnActions(bus), {
        spawnContext: null,
        modelActions: createHarnessModelsActions(bus),
        worktreeActions: createWorktreeOptionsActions(bus),
      }),
    );
    const { stdin } = render(<Harness stores={stores} />);
    await tick();

    // Drive the full flow so the spawn actually submits (and rejects).
    stdin.write('\r'); // harness (claude_code)
    await tick();
    stdin.write('\r'); // model
    await tick();
    stdin.write('\r'); // effort
    await tick();
    stdin.write('\r'); // worktree (main)
    await tick();
    stdin.write('\r'); // name (blank) → submit
    await tick();

    expect(selectActiveMode(stores.modes)).toBeNull(); // dismissed (exit-then-act)
    await tick();
    await tick();

    const errs = errorToasts();
    expect(errs).toHaveLength(1);
    expect(errs[0]?.text).toBe('rpc error [internal]: spawn failed');
  });

  it('antigravity skips BOTH model and effort steps', async () => {
    const { stores, bus, enter } = setup(null);
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();
    // Move cursor to antigravity (index 4): claude_code, codex, cursor, pi, antigravity.
    for (let i = 0; i < 4; i++) {
      stdin.write('j');
      await tick();
    }
    expect(lastFrame()).toContain('› antigravity');
    stdin.write('\r'); // confirm harness → next active step is worktree (model+effort skipped)
    await tick();
    expect(lastFrame()).toContain('Select worktree');
    expect(lastFrame()).not.toContain('Select model');
    expect(lastFrame()).not.toContain('Select effort');
    stdin.write('\r'); // worktree (main)
    await tick();
    stdin.write('\r'); // name (blank) → submit
    await tick();
    await tick();
    await tick();
    const payload = spawnSubmitPayload(bus);
    expect(payload['harness']).toBe('antigravity');
    expect(payload).not.toHaveProperty('effort'); // no effort enum → omitted
    // Model-step skipped → model is '' (NOT a Claude id like 'sonnet'). The live handler tolerates
    // an empty string and lets the adapter pick its own default; forcing a Claude id would be the
    // same invalid-id bug class this rewrite fixes for the harness field.
    expect(payload['model']).toBe('');
  });

  it('cursor skips the model step but keeps effort (slow/fast)', async () => {
    const { stores, bus, enter } = setup(null);
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();
    stdin.write('j'); // codex
    await tick();
    stdin.write('j'); // cursor
    await tick();
    expect(lastFrame()).toContain('› cursor');
    stdin.write('\r'); // confirm harness → effort (model skipped)
    await tick();
    expect(lastFrame()).toContain('Select effort');
    expect(lastFrame()).toContain('slow');
    expect(lastFrame()).toContain('fast');
    expect(lastFrame()).not.toContain('Select model');
    stdin.write('\r'); // effort (slow, the default)
    await tick();
    stdin.write('\r'); // worktree (main)
    await tick();
    stdin.write('\r'); // name → submit
    await tick();
    await tick();
    await tick();
    expect(spawnSubmitPayload(bus)).toMatchObject({ harness: 'cursor', effort: 'slow' });
  });

  it('codex shows its model list and threads the selected model id', async () => {
    const { stores, bus, enter } = setup(null);
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();
    stdin.write('j'); // codex
    await tick();
    expect(lastFrame()).toContain('› codex');
    stdin.write('\r'); // confirm harness → model step (codex has a static model list)
    await tick();
    expect(lastFrame()).toContain('Select model');
    expect(lastFrame()).toContain('GPT-5.5'); // first codex model label, cursor at index 0
    stdin.write('\r'); // select gpt-5.5
    await tick();
    expect(lastFrame()).toContain('Select effort'); // codex has an effort enum
    stdin.write('\r'); // effort medium (default)
    await tick();
    stdin.write('\r'); // worktree main
    await tick();
    stdin.write('\r'); // name → submit
    await tick();
    await tick();
    await tick();
    expect(spawnSubmitPayload(bus)).toMatchObject({
      harness: 'codex',
      model: 'gpt-5.5',
      effort: 'medium',
    });
  });

  it('"+ new worktree" inserts a branch step, validates non-empty, threads worktree_branch', async () => {
    const { stores, bus, enter } = setup(null);
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();
    stdin.write('\r'); // harness claude_code
    await tick();
    stdin.write('\r'); // model sonnet
    await tick();
    stdin.write('\r'); // effort medium
    await tick();
    // worktree step: [main, +new]. Move to "+ new worktree".
    expect(lastFrame()).toContain('+ new worktree');
    stdin.write('j');
    await tick();
    expect(lastFrame()).toContain('› + new worktree');
    stdin.write('\r'); // confirm new worktree → branch step
    await tick();
    expect(lastFrame()).toContain('branch name');

    // Empty branch → validation error, stays on branch step.
    stdin.write('\r');
    await tick();
    expect(lastFrame()).toContain('Branch name is required');
    expect(lastFrame()).toContain('branch name'); // still on branch step

    // Type a branch (letters route through onUncaptured even though some are j/k/y/n bound).
    for (const ch of 'my-feat') {
      stdin.write(ch);
      await tick();
    }
    expect(lastFrame()).toContain('my-feat');
    stdin.write('\r'); // confirm branch → name step
    await tick();
    expect(lastFrame()).toContain('Rogue name');
    stdin.write('\r'); // name blank → submit
    await tick();
    await tick();
    await tick();
    expect(spawnSubmitPayload(bus)).toMatchObject({ worktree_branch: 'my-feat' });
  });

  it('a typed rogue name threads through as `name`', async () => {
    const { stores, bus, enter } = setup(null);
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();
    stdin.write('\r'); // harness
    await tick();
    stdin.write('\r'); // model
    await tick();
    stdin.write('\r'); // effort
    await tick();
    stdin.write('\r'); // worktree main
    await tick();
    expect(lastFrame()).toContain('Rogue name');
    for (const ch of 'jay') {
      // includes 'j' + 'y' — must be typed literally, not navigate/submit
      stdin.write(ch);
      await tick();
    }
    expect(lastFrame()).toContain('jay');
    stdin.write('\r'); // submit
    await tick();
    await tick();
    await tick();
    expect(spawnSubmitPayload(bus)).toMatchObject({ name: 'jay' });
  });

  it('context step appears LAST when a doc is focused; y → reference-by-path kickoff', async () => {
    const { stores, bus, enter } = setup(TEST_CONTEXT);
    const onSubmit = vi.fn();
    enter({ onSubmit });
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    await tick();
    stdin.write('\r'); // harness
    await tick();
    stdin.write('\r'); // model
    await tick();
    stdin.write('\r'); // effort
    await tick();
    stdin.write('\r'); // worktree
    await tick();
    stdin.write('\r'); // name (blank) → context (because hasContext)
    await tick();
    expect(lastFrame()).toContain('my-note');
    expect(lastFrame()).toContain('[yes]');
    stdin.write('y'); // accept context → submit
    await tick();

    expect(selectActiveMode(stores.modes)).toBeNull();
    await tick();
    await tick();
    expect(submitKinds(bus)).toEqual(['crow.spawn_rogue', 'agent.message']);
    expect(kickoffSubmitPayload(bus)).toMatchObject({
      agent_id: 'rogue-001',
      message: `Please read ${TEST_CONTEXT.path} before starting.`,
    });
  });

  it('context step is a navigable radio: l/→ moves to "no", Enter submits the highlight (item 7)', async () => {
    const { stores, bus, enter } = setup(TEST_CONTEXT);
    enter();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    await tick();
    for (let i = 0; i < 5; i++) {
      stdin.write('\r'); // harness, model, effort, worktree, name → context
      await tick();
    }
    expect(lastFrame()).toContain('[yes]'); // highlight starts on yes
    stdin.write('l'); // move highlight to "no" (does NOT submit)
    await tick();
    expect(lastFrame()).toContain('[no]');
    expect(selectActiveMode(stores.modes)?.id).toBe(SPAWN_WIZARD_MODE_ID); // still up
    stdin.write('\r'); // Enter submits the highlighted "no" → no kickoff
    await tick();
    await tick();
    await tick();
    expect(submitKinds(bus)).toEqual(['crow.spawn_rogue']);
    expect(kickoffSubmitPayload(bus)).toBeUndefined();
  });

  it('context step arrows move the highlight: → then ← returns to "yes" and Enter includes', async () => {
    const { stores, bus, enter } = setup(TEST_CONTEXT);
    enter();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    await tick();
    for (let i = 0; i < 5; i++) {
      stdin.write('\r');
      await tick();
    }
    stdin.write('\x1b[C'); // right arrow → no
    await tick();
    expect(lastFrame()).toContain('[no]');
    stdin.write('\x1b[D'); // left arrow → yes
    await tick();
    expect(lastFrame()).toContain('[yes]');
    stdin.write('\r'); // Enter submits the highlighted "yes" → kickoff included
    await tick();
    await tick();
    await tick();
    expect(submitKinds(bus)).toEqual(['crow.spawn_rogue', 'agent.message']);
  });

  it('context step n declines — no kickoff agent.message', async () => {
    const { stores, bus, enter } = setup(TEST_CONTEXT);
    enter();
    const { stdin } = render(<Harness stores={stores} />);
    await tick();
    for (let i = 0; i < 5; i++) {
      stdin.write('\r'); // harness, model, effort, worktree, name
      await tick();
    }
    stdin.write('n'); // decline
    await tick();
    await tick();
    await tick();
    expect(submitKinds(bus)).toEqual(['crow.spawn_rogue']);
    expect(kickoffSubmitPayload(bus)).toBeUndefined();
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
    stdin.write('\x01'); // ctrl+1
    await tick();
    expect(selectActiveMode(stores.modes)?.id).toBe(SPAWN_WIZARD_MODE_ID);
    expect(stores.panels.getState().visible.has('plans')).toBe(false);
  });
});

/**
 * H4 (F11 guard) — spawn-wizard payload contract. Pins the REAL spawn action payload to the live
 * handler's required schema. Extended from F11 with worktree threading.
 */
describe('H4 — spawn payload contract (real spawn action)', () => {
  const REQUIRED_SPAWN_FIELDS = ['harness', 'model'] as const;

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
      harness: 'claude_code',
      model: 'sonnet',
      effort: 'medium',
    });
    const payload = spawnSubmitPayload(bus);
    for (const field of REQUIRED_SPAWN_FIELDS) {
      expect(payload[field], `spawn payload missing required field "${field}"`).toBeTruthy();
    }
    expect(payload).not.toHaveProperty('kickoff_message');
  });

  it('delivers a supplied kickoff out-of-band as agent.message (not silently dropped)', async () => {
    const bus = liveStubBus();
    await createSpawnActions(bus).spawnRogue({
      harness: 'claude_code',
      model: 'sonnet',
      kickoffMessage: 'Please read .murder/notes/x.md before starting.',
    });
    expect(submitKinds(bus)).toEqual(['crow.spawn_rogue', 'agent.message']);
    expect(kickoffSubmitPayload(bus)).toMatchObject({
      agent_id: 'rogue-001',
      message: 'Please read .murder/notes/x.md before starting.',
    });
  });

  it('auto-opens the spawned rogue chat pane + pins it active when a store is supplied (item 9e)', async () => {
    const bus = liveStubBus();
    bus.stubRpc('state.crow_snapshot', { invalidation_key: 'iv', sessions: [] });
    bus.stubRpc('state.conversations_snapshot', {
      conversations: [],
      as_of: '',
      invalidation_key: 'iv',
    });
    const { store, dispose } = createAppStore(bus);
    const result = await createSpawnActions(bus, store).spawnRogue({
      harness: 'claude_code',
      model: 'sonnet',
    });
    expect(result.agent_id).toBe('rogue-001');
    expect(store.getState().conversations.paneOverrides.get('rogue-001')).toBe(true);
    expect(store.getState().conversations.activePaneAgentId).toBe('rogue-001');
    dispose();
  });

  it('threads worktree_branch / worktree_path (branch wins) and omits both when absent', async () => {
    const busBranch = liveStubBus();
    await createSpawnActions(busBranch).spawnRogue({
      harness: 'claude_code',
      model: 'sonnet',
      worktreeBranch: 'feat/x',
    });
    expect(spawnSubmitPayload(busBranch)).toMatchObject({ worktree_branch: 'feat/x' });
    expect(spawnSubmitPayload(busBranch)).not.toHaveProperty('worktree_path');

    const busPath = liveStubBus();
    await createSpawnActions(busPath).spawnRogue({
      harness: 'claude_code',
      model: 'sonnet',
      worktreePath: '/wt/x',
    });
    expect(spawnSubmitPayload(busPath)).toMatchObject({ worktree_path: '/wt/x' });

    const busNone = liveStubBus();
    await createSpawnActions(busNone).spawnRogue({ harness: 'claude_code', model: 'sonnet' });
    expect(spawnSubmitPayload(busNone)).not.toHaveProperty('worktree_path');
    expect(spawnSubmitPayload(busNone)).not.toHaveProperty('worktree_branch');
  });

  it('fires no follow-up command when kickoff is empty or absent', async () => {
    const busEmpty = liveStubBus();
    await createSpawnActions(busEmpty).spawnRogue({
      harness: 'claude_code',
      model: 'sonnet',
      kickoffMessage: '',
    });
    expect(submitKinds(busEmpty)).toEqual(['crow.spawn_rogue']);

    const busAbsent = liveStubBus();
    await createSpawnActions(busAbsent).spawnRogue({ harness: 'claude_code', model: 'sonnet' });
    expect(submitKinds(busAbsent)).toEqual(['crow.spawn_rogue']);
  });
});

describe('spawnWizardHints — bottom-bar hints per step (item 4b/4c)', () => {
  it('list steps advertise j/k nav + confirm + cancel', () => {
    for (const step of ['harness', 'model', 'effort', 'worktree'] as const) {
      const keys = spawnWizardHints(step).map((h) => h.key);
      expect(keys).toContain('j/k');
      expect(keys).toContain('enter');
      expect(keys).toContain('esc');
    }
  });

  it('text steps drop nav but keep confirm + cancel', () => {
    for (const step of ['branch', 'name'] as const) {
      const keys = spawnWizardHints(step).map((h) => h.key);
      expect(keys).not.toContain('j/k');
      expect(keys).toEqual(['enter', 'esc']);
    }
  });

  it('context step advertises h/l nav + y/n + confirm + cancel (item 7)', () => {
    const keys = spawnWizardHints('context').map((h) => h.key);
    expect(keys).toEqual(['h/l', 'enter', 'y/n', 'esc']);
  });
});

describe('spawn wizard — hints moved out of the modal box (item 4c)', () => {
  it('the modal no longer renders the inline hint line; the mode supplies bar hints instead', async () => {
    const { stores, enter } = setup();
    enter();
    const { lastFrame } = render(<Harness stores={stores} />);
    await tick();
    // The old hardcoded modal hint line is gone.
    expect(lastFrame()).not.toContain('j/k: navigate · enter: confirm · esc: cancel');
    // The mode advertises its hints to the bottom bar instead.
    expect(selectActiveMode(stores.modes)?.hints?.map((h) => h.key)).toContain('j/k');
  });
});

describe('alt+s dispatcher test', () => {
  it('alt+s fires the spawn handler when CHAT is focused', async () => {
    const stores = createInputStores([], 'chat');
    const spawnFn = vi.fn();
    const { stdin } = render(<Harness stores={stores} spawn={spawnFn} />);
    await tick();
    stdin.write('\x1bs'); // alt+s
    await tick();
    expect(spawnFn).toHaveBeenCalledOnce();
  });

  it('alt+s does NOT fire spawn while wizard is already up (exclusive capture)', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const spawnFn = vi.fn();
    const bus = new FakeBusClient();
    bus.stubRpc('command.submit', { ok: true, command_id: 'cmd-1' });
    const actions = createSpawnActions(bus);
    stores.modes.getState().enter(spawnWizardMode(stores.modes, actions, { spawnContext: null }));
    const { stdin } = render(<Harness stores={stores} spawn={spawnFn} />);
    await tick();
    stdin.write('\x1bs');
    await tick();
    expect(spawnFn).not.toHaveBeenCalled();
    expect(selectActiveMode(stores.modes)?.id).toBe(SPAWN_WIZARD_MODE_ID);
  });
});
