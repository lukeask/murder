/**
 * UsagePanel test — copied from RosterPanel.test.tsx per the C5 copy recipe.
 *
 * Tests:
 *  - Renders usage gauge rows with harness + bar + pct + reset label.
 *  - Focus highlight behaviour.
 *  - Cursor navigation keys (j/k) fire only when focused.
 *  - Empty state chrome ("no usage data").
 */

import { Box } from 'ink';
import { render } from 'ink-testing-library';
import type { JSX } from 'react';
import { describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { UsagePanel } from '../../src/components/UsagePanel.js';
import { AppStoreProvider } from '../../src/hooks/useAppStore.js';
import { InputStoresProvider } from '../../src/hooks/useInputStores.js';
import { useRootInput } from '../../src/hooks/useRootInput.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import { createAppStore } from '../../src/store/store.js';
import type { UsageSnapshotReply } from '../../src/store/usage/usageActions.js';

const CTRL_F = '\x06';

async function tick(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 20));
}

function twoGauges(): UsageSnapshotReply {
  return {
    invalidation_key: 'iv',
    gauges: [
      {
        harness: 'claude',
        window_key: 'h1',
        pct: 65,
        t_until_reset_minutes: 20,
        t_period_minutes: 60,
      },
      {
        harness: 'codex',
        window_key: 'h1',
        pct: 30,
        t_until_reset_minutes: 5,
        t_period_minutes: 60,
      },
    ],
  };
}

function RootInput(): null {
  useRootInput();
  return null;
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
        <Box>
          <UsagePanel />
        </Box>
      </InputStoresProvider>
    </AppStoreProvider>
  );
}

async function setup(reply: UsageSnapshotReply = twoGauges(), focused = true) {
  const fake = new FakeBusClient();
  fake.stubRpc('crow.get_snapshot', {
    invalidation_key: 'iv',
    sessions: [],
  });
  fake.stubRpc('usage.get_snapshot', reply);
  const { store, dispose } = createAppStore(fake);
  await store.getState().actions.usage.refresh();
  const inputStores = createInputStores(['usage'], focused ? 'usage' : 'chat');
  return { fake, store, dispose, inputStores };
}

describe('UsagePanel — rendering', () => {
  it('renders gauge rows with harness, pct, and reset time', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    // Both harnesses appear.
    expect(frame).toContain('claude');
    expect(frame).toContain('codex');
    // Formatted pct labels.
    expect(frame).toContain('65%');
    expect(frame).toContain('30%');
    // Reset time labels.
    expect(frame).toContain('20m');
    expect(frame).toContain('5m');
    dispose();
  });

  it('renders higher usage gauge first (sorted by pct desc)', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    // 'claude' (65%) should appear before 'codex' (30%).
    expect(frame.indexOf('claude')).toBeLessThan(frame.indexOf('codex'));
    dispose();
  });

  it('shows the focus highlight only when it is the effective focus', async () => {
    const focused = await setup(twoGauges(), true);
    render(<Harness store={focused.store} inputStores={focused.inputStores} />);
    await tick();
    expect(focused.inputStores.focus.getState().intendedId).toBe('usage');
    focused.dispose();

    const unfocused = await setup(twoGauges(), false);
    render(<Harness store={unfocused.store} inputStores={unfocused.inputStores} />);
    await tick();
    expect(unfocused.inputStores.focus.getState().intendedId).toBe('chat');
    unfocused.dispose();
  });

  it('moves the cursor on j only when focused', async () => {
    const { store, inputStores, dispose } = await setup(twoGauges(), true);
    const { stdin, lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    // Initially cursor on row 0 (claude, highest pct).
    const before = lastFrame() ?? '';
    const markerPos = before.indexOf('▌');
    const claudePos = before.indexOf('claude');
    expect(markerPos).toBeLessThan(claudePos + 20);

    // Press j → cursor moves to row 1 (codex).
    stdin.write('j');
    await tick();
    const afterDown = lastFrame() ?? '';
    expect(afterDown.indexOf('▌')).toBeGreaterThan(afterDown.indexOf('claude'));

    // Unfocus; k should not affect the cursor.
    stdin.write(CTRL_F);
    await tick();
    const beforeUnfocused = lastFrame() ?? '';
    stdin.write('k');
    await tick();
    expect(lastFrame()).toBe(beforeUnfocused);
    dispose();
  });

  it('renders empty chrome when the slice has no rows', async () => {
    const { store, inputStores, dispose } = await setup(
      { invalidation_key: 'iv', gauges: [] },
      true,
    );
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    expect(lastFrame()).toContain('no usage data');
    dispose();
  });
});
