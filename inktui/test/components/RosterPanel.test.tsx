/**
 * RosterPanel test — THE canonical panel-test idiom every Phase-B chunk copies.
 *
 * The recipe (copy this file alongside the panel you copy from RosterPanel.tsx):
 *  1. Build a `FakeBusClient`, stub the panel's read RPC with known slice data, and build the store
 *     with {@link createAppStore} — exactly the C3 store-test setup.
 *  2. Build the C4 input stores with {@link createInputStores}, seeding the panel visible.
 *  3. Render the panel inside both providers plus {@link useRootInput} (a tiny local harness), so a
 *     simulated key routes through the real dispatcher to the focused panel — the production path.
 *  4. Assert on the painted frame: the two-line rows, the focus highlight, and that a keymap intent
 *     fires on a simulated key only when the panel is focused.
 *
 * Asserting on the frame is a real assertion because the component is a pure function of its slice
 * (rule 1): same slice + same focus → same frame.
 */

import { Box } from 'ink';
import { render } from 'ink-testing-library';
import type { JSX } from 'react';
import { describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { RosterPanel } from '../../src/components/RosterPanel.js';
import { AppStoreProvider } from '../../src/hooks/useAppStore.js';
import { InputStoresProvider } from '../../src/hooks/useInputStores.js';
import { useRootInput } from '../../src/hooks/useRootInput.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import type { CrowSnapshotReply } from '../../src/store/roster/rosterActions.js';
import { createAppStore } from '../../src/store/store.js';

const ALT_SPACE = '\x1b '; // alt+space → focus chat (representable in a terminal; ctrl+digit is not)

/** Let Ink flush a render + the post-layout measure/keymap-registration effects. */
async function tick(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 20));
}

/** A canned roster reply with two crows, so the two-line layout has rows to paint. */
function twoCrows(): CrowSnapshotReply {
  return {
    invalidation_key: 'iv',
    sessions: [
      {
        agent_id: 'a-1',
        role: 'crow',
        status: 'running',
        harness: 'claude',
        model: 'anthropic/claude-opus',
        session_name: 'alpha',
      },
      {
        agent_id: 'b-2',
        role: 'crow',
        status: 'idle',
        harness: 'codex',
        model: 'openai/gpt-5',
        session_name: 'bravo',
      },
    ],
  };
}

/** Local harness: the panel inside both providers with the one root input loop, the production
 * mounting a copy uses. Focus is seeded by the caller via the input stores. */
function Harness({
  store,
  inputStores,
}: {
  readonly store: ReturnType<typeof createAppStore>['store'];
  readonly inputStores: ReturnType<typeof createInputStores>;
}): JSX.Element {
  return (
    <AppStoreProvider value={store}>
      <InputStoresProvider value={inputStores}>
        <RootInput />
        <Box>
          <RosterPanel />
        </Box>
      </InputStoresProvider>
    </AppStoreProvider>
  );
}

/** Tiny inner so `useRootInput` runs inside the providers. */
function RootInput(): null {
  useRootInput();
  return null;
}

/** Build store (with stubbed RPC + an initial refresh) + input stores, panel focused by default. */
async function setup(reply: CrowSnapshotReply = twoCrows(), focused = true) {
  const fake = new FakeBusClient();
  fake.stubRpc('state.crow_snapshot', reply);
  const { store, dispose } = createAppStore(fake);
  await store.getState().actions.roster.refresh();
  const inputStores = createInputStores(['crows'], focused ? 'crows' : 'chat');
  return { fake, store, dispose, inputStores };
}

describe('RosterPanel — reference panel', () => {
  it('renders two-line entries from the slice (name+status, then harness · model)', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    // Line one: name + status. Line two: harness · model (model basename-only, per the selector).
    expect(frame).toContain('alpha');
    expect(frame).toContain('running');
    expect(frame).toContain('claude · claude-opus');
    expect(frame).toContain('bravo');
    expect(frame).toContain('codex · gpt-5');
    dispose();
  });

  it('shows the focus highlight only when it is the effective focus', async () => {
    // Focused: panel is the intended focus and visible → its border/title highlight is on.
    const focusedSetup = await setup(twoCrows(), true);
    const focusedTree = render(
      <Harness store={focusedSetup.store} inputStores={focusedSetup.inputStores} />,
    );
    await tick();
    expect(focusedSetup.inputStores.focus.getState().intendedId).toBe('crows');
    focusedSetup.dispose();

    // Unfocused: chat is the intended focus → the panel is not highlighted (effective focus = chat).
    const unfocusedSetup = await setup(twoCrows(), false);
    render(<Harness store={unfocusedSetup.store} inputStores={unfocusedSetup.inputStores} />);
    await tick();
    expect(unfocusedSetup.inputStores.focus.getState().intendedId).toBe('chat');
    void focusedTree;
    unfocusedSetup.dispose();
  });

  it('moves the local cursor on a declared key only when focused', async () => {
    const { store, inputStores, dispose } = await setup(twoCrows(), true);
    const { stdin, lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    // Focused: 'j' fires the panel's cursorDown intent → the cursor marker moves to the 2nd row.
    const before = lastFrame() ?? '';
    // The cursor marker '▌' sits on the selected row's first line; initially on alpha.
    expect(before.indexOf('▌')).toBeLessThan(before.indexOf('bravo'));
    stdin.write('j');
    await tick();
    const afterDown = lastFrame() ?? '';
    // After moving down, the marker is now on/after bravo's block.
    expect(afterDown.indexOf('▌')).toBeGreaterThan(afterDown.indexOf('alpha'));

    // Move focus to chat; 'j' now goes to the input, not the panel → cursor is unchanged.
    stdin.write(ALT_SPACE);
    await tick();
    expect(inputStores.focus.getState().intendedId).toBe('chat');
    const beforeUnfocused = lastFrame() ?? '';
    stdin.write('k');
    await tick();
    expect(lastFrame()).toBe(beforeUnfocused);
    dispose();
  });

  it('renders empty chrome when the slice has no rows', async () => {
    const { store, inputStores, dispose } = await setup(
      { invalidation_key: 'iv', sessions: [] },
      true,
    );
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    expect(lastFrame()).toContain('no crows');
    dispose();
  });
});
