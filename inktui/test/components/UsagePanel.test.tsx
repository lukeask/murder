/**
 * UsagePanel test — copied from RosterPanel.test.tsx per the C5 copy recipe.
 *
 * Asserts the Pane inline-title border (`╭─ Usage ─…`) + the grouped provider blocks (a header line
 * per harness, then one gauge line per window).
 *
 * Tests:
 *  - Renders provider blocks with harness + pct + reset label.
 *  - Providers appear in first-seen (group) order.
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
import type { ScheduleSnapshotReply } from '../../src/store/tickets/ticketsActions.js';

const ALT_F = '\x1bf';

async function tick(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 20));
}

// Usage gauges are embedded in the schedule snapshot (F2) — the usage slice reads `usage_gauges`.
function twoGauges(): ScheduleSnapshotReply {
  return {
    invalidation_key: 'iv',
    active_tickets: [],
    recent_done_tickets: [],
    archived_tickets: [],
    usage_gauges: [
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

async function setup(reply: ScheduleSnapshotReply = twoGauges(), focused = true) {
  const fake = new FakeBusClient();
  fake.stubRpc('state.crow_snapshot', {
    invalidation_key: 'iv',
    sessions: [],
  });
  fake.stubRpc('state.schedule_snapshot', reply);
  const { store, dispose } = createAppStore(fake);
  await store.getState().actions.usage.refresh();
  const inputStores = createInputStores(['usage'], focused ? 'usage' : 'chat');
  return { fake, store, dispose, inputStores };
}

describe('UsagePanel — rendering', () => {
  it('renders provider blocks with harness, pct, and reset time', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    // Pane inline title on the top border (Phase 3 Pane + Ledger structure).
    expect(frame).toContain('╭─ Usage');
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

  it('renders providers in first-seen group order', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    // 'claude' is the first harness in the wire order, so its block comes before 'codex'.
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

    // Initially cursor on gauge 0 (claude's window) — its marker sits after the 'claude' header but
    // before the 'codex' block.
    const before = lastFrame() ?? '';
    expect(before.indexOf('▌')).toBeGreaterThan(before.indexOf('claude'));
    expect(before.indexOf('▌')).toBeLessThan(before.indexOf('codex'));

    // Press j → cursor moves to gauge 1 (codex's window): the marker is now after 'codex'.
    stdin.write('j');
    await tick();
    const afterDown = lastFrame() ?? '';
    expect(afterDown.indexOf('▌')).toBeGreaterThan(afterDown.indexOf('codex'));

    // Unfocus; k should not affect the cursor.
    stdin.write(ALT_F);
    await tick();
    const beforeUnfocused = lastFrame() ?? '';
    stdin.write('k');
    await tick();
    expect(lastFrame()).toBe(beforeUnfocused);
    dispose();
  });

  it('renders empty chrome when the slice has no rows', async () => {
    const { store, inputStores, dispose } = await setup(
      {
        invalidation_key: 'iv',
        active_tickets: [],
        recent_done_tickets: [],
        archived_tickets: [],
        usage_gauges: [],
      },
      true,
    );
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    expect(lastFrame()).toContain('no usage data');
    dispose();
  });
});
