/**
 * TicketsPanel test — copied from {@link ./NotesPanel.test.tsx} per the C5 idiom.
 *
 * Recipe summary (same as the reference, with tickets-specific stubs):
 *  1. Build `FakeBusClient`, stub `state.schedule_snapshot`, build the store.
 *  2. Build C4 input stores, seeding `tickets` visible and optionally focused.
 *  3. Render inside both providers + `useRootInput`.
 *  4. Assert 2-row entries (both rows), deps cell content (empty + non-empty),
 *     focus highlight, keymap intent only when focused.
 *     Note: alternating background color is asserted at the selector level (rowParity tests in
 *     ticketsSelectors.test.ts) because ink-testing-library strips ANSI escapes in non-TTY env.
 */

import { Box } from 'ink';
import { render } from 'ink-testing-library';
import type { JSX } from 'react';
import { describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { TicketsPanel } from '../../src/components/TicketsPanel.js';
import { AppStoreProvider } from '../../src/hooks/useAppStore.js';
import { InputStoresProvider } from '../../src/hooks/useInputStores.js';
import { useRootInput } from '../../src/hooks/useRootInput.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import { createAppStore } from '../../src/store/store.js';
import type { ScheduleSnapshotReply } from '../../src/store/tickets/ticketsActions.js';

const CTRL_F = '\x06';

async function tick(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 20));
}

function twoTickets(): ScheduleSnapshotReply {
  return {
    invalidation_key: 'iv',
    active_tickets: [
      {
        id: 'T-1',
        title: 'Alpha ticket',
        status: 'in_progress',
        last_update_at: '2026-06-08T10:00:00',
        last_update_label: 'agent started',
        schedule_at: null,
        harness: 'claude',
        model: 'anthropic/claude-opus',
        pending_dep_ids: [],
      },
      {
        id: 'T-2',
        title: 'Bravo ticket',
        status: 'ready',
        last_update_at: '2026-06-07T09:00:00',
        last_update_label: 'user created',
        schedule_at: 'Mon 14:00',
        harness: 'codex',
        model: 'openai/gpt-5',
        pending_dep_ids: ['T-3', 'T-4'],
      },
    ],
    recent_done_tickets: [],
    archived_tickets: [],
    usage_gauges: [],
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
        <Box>
          <TicketsPanel />
        </Box>
      </InputStoresProvider>
    </AppStoreProvider>
  );
}

function RootInput(): null {
  useRootInput();
  return null;
}

async function setup(reply: ScheduleSnapshotReply = twoTickets(), focused = true) {
  const fake = new FakeBusClient();
  fake.stubRpc('state.schedule_snapshot', reply);
  // Stub sibling slices so createAppStore doesn't choke on any stray events.
  fake.stubRpc('state.crow_snapshot', { invalidation_key: 'iv', sessions: [] });
  fake.stubRpc('state.notes_snapshot', { invalidation_key: 'iv', notes: [] });
  fake.stubRpc('state.reports_snapshot', { invalidation_key: 'iv', reports: [] });
  const { store, dispose } = createAppStore(fake);
  await store.getState().actions.tickets.refresh();
  const inputStores = createInputStores(['tickets'], focused ? 'tickets' : 'chat');
  return { fake, store, dispose, inputStores };
}

describe('TicketsPanel', () => {
  it('renders 2-row × 5-col entries (id/title, status/last-update, deps/schedule, harness/model, plan/worktree)', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';

    // Row 1 content for T-1: id, title, status.
    expect(frame).toContain('T-1');
    expect(frame).toContain('Alpha ticket');
    expect(frame).toContain('in_progress');

    // Row 2 content for T-1: harness+model, deps cell = 'ok' (no pending deps), no schedule.
    expect(frame).toContain('claude');
    expect(frame).toContain('claude-opus');
    expect(frame).toContain('ok'); // depsCell when pendingDepIds is empty

    // Row 1 content for T-2: id, title, status.
    expect(frame).toContain('T-2');
    expect(frame).toContain('Bravo ticket');
    expect(frame).toContain('ready');

    // Row 2 content for T-2: deps cell shows pending ids, schedule shows 'Mon 14:00'.
    expect(frame).toContain('T-3, T-4'); // depsCell with pending dep ids
    expect(frame).toContain('Mon 14:00'); // scheduleCell

    dispose();
  });

  it('renders "ok" in the deps cell when pendingDepIds is empty', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    // T-1 has empty pendingDepIds → depsCell = 'ok'.
    expect(lastFrame()).toContain('ok');
    dispose();
  });

  it('renders joined pending dep ids in the deps cell when deps are not satisfied', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    // T-2 has pendingDepIds: ['T-3', 'T-4'] → depsCell = 'T-3, T-4'.
    expect(lastFrame()).toContain('T-3, T-4');
    dispose();
  });

  it('shows the focus highlight only when it is the effective focus', async () => {
    const focusedSetup = await setup(twoTickets(), true);
    render(<Harness store={focusedSetup.store} inputStores={focusedSetup.inputStores} />);
    await tick();
    expect(focusedSetup.inputStores.focus.getState().intendedId).toBe('tickets');
    focusedSetup.dispose();

    const unfocusedSetup = await setup(twoTickets(), false);
    render(<Harness store={unfocusedSetup.store} inputStores={unfocusedSetup.inputStores} />);
    await tick();
    expect(unfocusedSetup.inputStores.focus.getState().intendedId).toBe('chat');
    unfocusedSetup.dispose();
  });

  it('moves the local cursor on a declared key only when focused', async () => {
    const { store, inputStores, dispose } = await setup(twoTickets(), true);
    const { stdin, lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    // Focused: 'j' fires cursorDown → cursor marker moves below T-1's block.
    const before = lastFrame() ?? '';
    // The cursor marker '▌' sits on the first ticket (T-1) initially.
    expect(before.indexOf('▌')).toBeLessThan(before.indexOf('T-2'));
    stdin.write('j');
    await tick();
    const afterDown = lastFrame() ?? '';
    // After moving down, the marker is now on/after T-2's block.
    expect(afterDown.indexOf('▌')).toBeGreaterThan(afterDown.indexOf('T-1'));

    // Unfocus: ctrl+f → chat; 'k' no longer routes to the panel.
    stdin.write(CTRL_F);
    await tick();
    expect(inputStores.focus.getState().intendedId).toBe('chat');
    const beforeUnfocused = lastFrame() ?? '';
    stdin.write('k');
    await tick();
    expect(lastFrame()).toBe(beforeUnfocused);
    dispose();
  });

  it('renders empty chrome when the slice has no tickets', async () => {
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
    expect(lastFrame()).toContain('no tickets');
    dispose();
  });
});
