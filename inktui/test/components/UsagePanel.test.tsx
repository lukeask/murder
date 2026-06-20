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

const ALT_SPACE = '\x1b '; // alt+space → focus chat (was alt+f, which now stars in panels)

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
  innerWidth,
}: {
  readonly store: ReturnType<typeof createAppStore>['store'];
  readonly inputStores: ReturnType<typeof createInputStores>;
  readonly innerWidth?: number;
}): JSX.Element {
  return (
    <AppStoreProvider value={store}>
      <InputStoresProvider value={inputStores}>
        <RootInput />
        <Box>
          <UsagePanel {...(innerWidth === undefined ? {} : { innerWidth })} />
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
    expect(frame).toContain('┏━ Usage');
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
    stdin.write(ALT_SPACE);
    await tick();
    const beforeUnfocused = lastFrame() ?? '';
    stdin.write('k');
    await tick();
    expect(lastFrame()).toBe(beforeUnfocused);
    dispose();
  });

  it('embeds the pct in the bar and drops the time marker', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    // 65% over a 12-cell bar fills 8 cells ≥ 3, so the label leads the fill: `65%` + 5 solid cells,
    // then the 4-cell track — CONTIGUOUS, with no time-through-period `│` marker cut into the bar.
    expect(frame).toContain('65%█████░░░░');
    // The pct column is gone from the key line (the label lives inside the bar now).
    expect(frame).not.toContain('pct');
    dispose();
  });

  it('right-aligns the pct on the grey track when the fill is under 3 cells', async () => {
    const reply = twoGauges();
    const { store, inputStores, dispose } = await setup({
      ...reply,
      usage_gauges: reply.usage_gauges.slice(0, 1).map((g) => ({ ...g, pct: 10 })),
    });
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    // 10% fills 1 of 12 cells (< 3): fill, then the grey track, then the right-aligned label.
    expect(lastFrame() ?? '').toContain('█░░░░░░░░10%');
    dispose();
  });

  it('sheds the win label, then the reset countdown, as the inner width narrows (R9)', async () => {
    // Full (default innerWidth 27 → bar 12 ≥ 8 with the whole trail): win + reset both shown.
    const full = await setup();
    const fullRender = render(<Harness store={full.store} inputStores={full.inputStores} />);
    await tick();
    const fullFrame = fullRender.lastFrame() ?? '';
    expect(fullFrame).toContain('win'); // key line labels the win column
    expect(fullFrame).toContain('1h'); // 60-minute window length
    expect(fullFrame).toContain('20m'); // reset countdown
    full.dispose();

    // Reset-only (innerWidth 20 → the full trail would squeeze the bar to 5 < 8; without win it is 9).
    const resetOnly = await setup();
    const resetRender = render(
      <Harness store={resetOnly.store} inputStores={resetOnly.inputStores} innerWidth={20} />,
    );
    await tick();
    const resetFrame = resetRender.lastFrame() ?? '';
    expect(resetFrame).not.toContain('win'); // window-length column dropped first
    expect(resetFrame).toContain('20m'); // reset countdown survives
    resetOnly.dispose();

    // Bare (innerWidth 8 → even reset alone would squeeze the bar under 8): the bar is all that's left.
    const bare = await setup();
    const bareRender = render(
      <Harness store={bare.store} inputStores={bare.inputStores} innerWidth={8} />,
    );
    await tick();
    const bareFrame = bareRender.lastFrame() ?? '';
    expect(bareFrame).not.toContain('20m'); // reset dropped last
    expect(bareFrame).not.toContain('reset'); // no key line at the bare layout
    expect(bareFrame).toContain('█'); // the bar is still drawn
    bare.dispose();
  });

  it('renders [paused]/[preferred] header tags per harness steering (RT5)', async () => {
    const reply = twoGauges();
    const { store, inputStores, dispose } = await setup({
      ...reply,
      usage_gauges: [
        {
          harness: 'claude',
          window_key: 'h1',
          pct: 65,
          t_until_reset_minutes: 20,
          t_period_minutes: 60,
          steering: 'pause',
        },
        {
          harness: 'codex',
          window_key: 'h1',
          pct: 30,
          t_until_reset_minutes: 5,
          t_period_minutes: 60,
          steering: 'prefer',
        },
      ],
    });
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    expect(frame).toContain('[paused]'); // claude is paused
    expect(frame).toContain('[preferred]'); // codex is preferred
    dispose();
  });

  it('renders no steering tag for auto (the default)', async () => {
    const { store, inputStores, dispose } = await setup(); // no steering field → auto
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    expect(frame).not.toContain('[paused]');
    expect(frame).not.toContain('[preferred]');
    dispose();
  });

  it('cycles steering on s: dispatches setSteering for the cursored gauge with the next value', async () => {
    const reply = twoGauges();
    const { fake, store, inputStores, dispose } = await setup({
      ...reply,
      // claude starts auto → s should request 'prefer'.
      usage_gauges: [
        {
          harness: 'claude',
          window_key: 'h1',
          pct: 65,
          t_until_reset_minutes: 20,
          t_period_minutes: 60,
          steering: 'auto',
        },
        {
          harness: 'codex',
          window_key: 'h1',
          pct: 30,
          t_until_reset_minutes: 5,
          t_period_minutes: 60,
          steering: 'auto',
        },
      ],
    });
    fake.stubRpc('command.submit', { ok: true, command_id: 'cmd-1' });
    fake.stubRpc('command.status', {
      ok: true,
      status: 'done',
      result_json: JSON.stringify({ handled: true, harness: 'claude', steering: 'prefer' }),
    });
    const { stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    // Cursor starts on gauge 0 (claude). Press the registry's panel.usageSteering chord (`s`).
    stdin.write('s');
    await tick();
    await tick();
    const submit = fake.rpcCalls.find((c) => c.method === 'command.submit');
    expect(submit?.params).toMatchObject({
      target_worker: 'scheduler',
      kind: 'scheduler.set_steering',
      payload: { harness: 'claude', steering: 'prefer' },
    });
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
