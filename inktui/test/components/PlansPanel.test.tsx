/**
 * PlansPanel test — the END-TO-END Pane + Ledger reference panel (Phase 2).
 *
 * Modeled on {@link ./NotesPanel.test.tsx} (the doc-panel recipe), but asserts the NEW layout
 * primitives rather than the old hand-rolled chrome:
 *  - the {@link ../../src/components/Pane.tsx Pane} draws the inline-title border (`╭─ Plans ─…`),
 *  - the {@link ../../src/components/Ledger.tsx Ledger} draws the two-line entries with the
 *    full-width selection highlight (the `▌` cursor marker on the selected row),
 *  - the panel keeps its local cursor + j/k keymap + star sort + focus wiring (rule 1).
 *
 * Recipe: stub the `state.plans_snapshot` RPC, build the store + C4 input stores (seeded `plans`
 * visible/focused), render inside both providers + the one `useRootInput`, then drive keys/assert.
 */

import { Box } from 'ink';
import { render } from 'ink-testing-library';
import type { JSX } from 'react';
import { describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { PlansPanel } from '../../src/components/PlansPanel.js';
import { AppStoreProvider } from '../../src/hooks/useAppStore.js';
import { InputStoresProvider } from '../../src/hooks/useInputStores.js';
import { useRootInput } from '../../src/hooks/useRootInput.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import type { PlansSnapshotReply } from '../../src/store/plans/plansActions.js';
import { createAppStore } from '../../src/store/store.js';

const ALT_F = '\x1bf';
const ALT_S = '\x1bs';

async function tick(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 20));
}

/** Two top-level plans (no parent). `alpha-plan` is more recent → sorts first by recency. */
function twoPlans(): PlansSnapshotReply {
  return {
    invalidation_key: 'iv',
    plans: [
      { name: 'alpha-plan', char_count: 1234, updated_at: '2026-06-08T10:00:00' },
      { name: 'bravo-plan', char_count: 567, updated_at: '2026-06-01T08:00:00' },
    ],
  };
}

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
        {/* Height-bounded like the live app's fullscreen layout, so the Ledger's self-measurement
            returns the AVAILABLE height (not the collapsed content height a bare Box yields under
            ink-testing-library) — this exercises the real measurement path. */}
        <Box height={24}>
          <PlansPanel />
        </Box>
      </InputStoresProvider>
    </AppStoreProvider>
  );
}

function RootInput(): null {
  useRootInput();
  return null;
}

async function setup(reply: PlansSnapshotReply = twoPlans(), focused = true) {
  const fake = new FakeBusClient();
  fake.stubRpc('state.plans_snapshot', reply);
  // Stub the unrelated snapshots/prefs RPCs so createAppStore + star persistence don't choke.
  fake.stubRpc('state.crow_snapshot', { invalidation_key: 'iv', sessions: [] });
  fake.stubRpc('tui.save_favorites', { ok: true, favorites: [] });
  const { store, dispose } = createAppStore(fake);
  await store.getState().actions.plans.refresh();
  const inputStores = createInputStores(['plans'], focused ? 'plans' : 'chat');
  return { fake, store, dispose, inputStores };
}

describe('PlansPanel (Pane + Ledger reference)', () => {
  it('renders the inline-title Pane border and two-line Ledger entries', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    // Pane inline title: `╭─ Plans ─…` on the top border (not a plain border + "Plans" text line).
    expect(frame).toContain('╭─ Plans');
    // Two-line entries: name on line 1, char count · formatted date on line 2 (selector-formatted).
    // The char count is right-padded to a fixed width for column alignment (CHAR_COUNT_FIELD_WIDTH),
    // so the `· date` follows pad spaces — assert the count and the date independently.
    expect(frame).toContain('alpha-plan');
    expect(frame).toContain('1,234 chars');
    expect(frame).toContain('· 2026-06-08 10:00');
    expect(frame).toContain('bravo-plan');
    expect(frame).toContain('567 chars');
    expect(frame).toContain('· 2026-06-01 08:00');
    dispose();
  });

  it('shows the focus highlight (cursor marker) only when it is the effective focus', async () => {
    const focusedSetup = await setup(twoPlans(), true);
    const focusedRender = render(
      <Harness store={focusedSetup.store} inputStores={focusedSetup.inputStores} />,
    );
    await tick();
    expect(focusedSetup.inputStores.focus.getState().intendedId).toBe('plans');
    // Focused → the Ledger paints the cursor marker on the selected (first) row.
    expect(focusedRender.lastFrame() ?? '').toContain('▌');
    focusedSetup.dispose();

    const unfocusedSetup = await setup(twoPlans(), false);
    const unfocusedRender = render(
      <Harness store={unfocusedSetup.store} inputStores={unfocusedSetup.inputStores} />,
    );
    await tick();
    expect(unfocusedSetup.inputStores.focus.getState().intendedId).toBe('chat');
    // Blurred → no cursor marker (the Ledger only highlights when focused).
    expect(unfocusedRender.lastFrame() ?? '').not.toContain('▌');
    unfocusedSetup.dispose();
  });

  it('moves the local cursor on a declared key only when focused', async () => {
    const { store, inputStores, dispose } = await setup(twoPlans(), true);
    const { stdin, lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    // Focused: cursor starts on alpha-plan; 'j' fires cursorDown → marker moves past alpha-plan.
    const before = lastFrame() ?? '';
    expect(before.indexOf('▌')).toBeLessThan(before.indexOf('bravo-plan'));
    stdin.write('j');
    await tick();
    const afterDown = lastFrame() ?? '';
    expect(afterDown.indexOf('▌')).toBeGreaterThan(afterDown.indexOf('alpha-plan'));

    // Unfocus: alt+f → chat; 'k' no longer routes to the panel (frame unchanged).
    stdin.write(ALT_F);
    await tick();
    expect(inputStores.focus.getState().intendedId).toBe('chat');
    const beforeUnfocused = lastFrame() ?? '';
    stdin.write('k');
    await tick();
    expect(lastFrame()).toBe(beforeUnfocused);
    dispose();
  });

  it('renders empty chrome when the slice has no plans (Ledger renders nothing for zero rows)', async () => {
    const { store, inputStores, dispose } = await setup(
      { invalidation_key: 'iv', plans: [] },
      true,
    );
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    expect(lastFrame()).toContain('no plans');
    dispose();
  });

  it('alt+s stars the highlighted plan: prefs RPC fires, ★ shows, sorts to top', async () => {
    // bravo-plan is older (sorts second). Move the cursor to it and star it; it must jump to the top
    // with a ★ marker, and tui.save_favorites must fire with its id.
    const { fake, store, inputStores, dispose } = await setup(twoPlans(), true);
    const { lastFrame, stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    stdin.write('j'); // cursor → bravo-plan
    await tick();
    stdin.write(ALT_S); // star the highlighted (bravo) plan
    await tick();

    const saveCalls = fake.rpcCalls.filter((c) => c.method === 'tui.save_favorites');
    expect(saveCalls.length).toBe(1);
    expect(saveCalls[0]?.params).toEqual({ favorites: ['bravo-plan'] });
    expect(store.getState().favorites.ids.has('bravo-plan')).toBe(true);

    // Starred-to-top: bravo-plan now renders above alpha-plan, with a ★ marker.
    const frame = lastFrame() ?? '';
    expect(frame).toContain('★');
    expect(frame.indexOf('bravo-plan')).toBeLessThan(frame.indexOf('alpha-plan'));
    dispose();
  });
});
