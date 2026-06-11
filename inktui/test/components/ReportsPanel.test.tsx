/**
 * ReportsPanel test — copied from {@link ./NotesPanel.test.tsx}.
 * Changes: uses `state.reports_snapshot`, panel id `'reports'`, empty chrome `'no reports'`.
 * Phase 3: asserts the Pane inline-title border (`╭─ Reports ─…`) + the Ledger two-line entries.
 */

import { Box } from 'ink';
import { render } from 'ink-testing-library';
import type { JSX } from 'react';
import { describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { ReportsPanel } from '../../src/components/ReportsPanel.js';
import { AppStoreProvider } from '../../src/hooks/useAppStore.js';
import { InputStoresProvider } from '../../src/hooks/useInputStores.js';
import { useRootInput } from '../../src/hooks/useRootInput.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import type { ReportsSnapshotReply } from '../../src/store/reports/reportsActions.js';
import { createAppStore } from '../../src/store/store.js';

const ALT_SPACE = '\x1b '; // alt+space → focus chat (was alt+f, which now stars in panels)

async function tick(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 20));
}

function twoReports(): ReportsSnapshotReply {
  return {
    invalidation_key: 'iv',
    reports: [
      {
        name: 'alpha-report',
        char_count: 9999,
        updated_at: '2026-06-07T12:00:00',
      },
      {
        name: 'bravo-report',
        char_count: 111,
        updated_at: '2026-05-15T09:30:00',
      },
    ],
  };
}

function manyReports(n: number): ReportsSnapshotReply {
  return {
    invalidation_key: 'iv',
    reports: Array.from({ length: n }, (_, i) => ({
      name: `report-${String(i).padStart(2, '0')}`,
      char_count: 100 + i,
      updated_at: '2026-06-07T12:00:00',
    })),
  };
}

function Harness({
  store,
  inputStores,
  height,
}: {
  readonly store: ReturnType<typeof createAppStore>['store'];
  readonly inputStores: ReturnType<typeof createInputStores>;
  readonly height?: number;
}): JSX.Element {
  return (
    <AppStoreProvider value={store}>
      <InputStoresProvider value={inputStores}>
        <RootInput />
        {/* When `height` is given, bound the pane so ink-testing-library's measureElement reports a
            small inner height — that forces the Ledger to window and the Pane to draw ▴/▾ indicators. */}
        <Box height={height}>
          <ReportsPanel />
        </Box>
      </InputStoresProvider>
    </AppStoreProvider>
  );
}

function RootInput(): null {
  useRootInput();
  return null;
}

async function setup(reply: ReportsSnapshotReply = twoReports(), focused = true) {
  const fake = new FakeBusClient();
  fake.stubRpc('state.reports_snapshot', reply);
  fake.stubRpc('state.crow_snapshot', { invalidation_key: 'iv', sessions: [] });
  const { store, dispose } = createAppStore(fake);
  await store.getState().actions.reports.refresh();
  const inputStores = createInputStores(['reports'], focused ? 'reports' : 'chat');
  return { fake, store, dispose, inputStores };
}

describe('ReportsPanel', () => {
  it('renders two-line entries (name, then charCount · updatedAt)', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    // Pane inline title on the top border (not a plain border + "Reports" text line).
    expect(frame).toContain('╭─ Reports');
    expect(frame).toContain('alpha-report');
    expect(frame).toContain('2026-06-07 12:00');
    expect(frame).toContain('bravo-report');
    expect(frame).toContain('2026-05-15 09:30');
    dispose();
  });

  it('shows the focus highlight only when it is the effective focus', async () => {
    const focusedSetup = await setup(twoReports(), true);
    render(<Harness store={focusedSetup.store} inputStores={focusedSetup.inputStores} />);
    await tick();
    expect(focusedSetup.inputStores.focus.getState().intendedId).toBe('reports');
    focusedSetup.dispose();

    const unfocusedSetup = await setup(twoReports(), false);
    render(<Harness store={unfocusedSetup.store} inputStores={unfocusedSetup.inputStores} />);
    await tick();
    expect(unfocusedSetup.inputStores.focus.getState().intendedId).toBe('chat');
    unfocusedSetup.dispose();
  });

  it('moves the local cursor on a declared key only when focused', async () => {
    const { store, inputStores, dispose } = await setup(twoReports(), true);
    const { stdin, lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    // Focused: 'j' fires cursorDown → cursor moves below alpha-report.
    const before = lastFrame() ?? '';
    expect(before.indexOf('▌')).toBeLessThan(before.indexOf('bravo-report'));
    stdin.write('j');
    await tick();
    const afterDown = lastFrame() ?? '';
    expect(afterDown.indexOf('▌')).toBeGreaterThan(afterDown.indexOf('alpha-report'));

    // Unfocus to chat; 'k' no longer routes to the panel.
    stdin.write(ALT_SPACE);
    await tick();
    expect(inputStores.focus.getState().intendedId).toBe('chat');
    const beforeUnfocused = lastFrame() ?? '';
    stdin.write('k');
    await tick();
    expect(lastFrame()).toBe(beforeUnfocused);
    dispose();
  });

  it('draws border scroll indicators (▴/▾) — not interior … — when the list overflows', async () => {
    // Many reports (2 lines each) into a height-bounded pane → the Ledger windows and the Pane's
    // top/bottom border carry the ▴/▾ overflow indicators. This is the end-to-end wiring: Ledger
    // onWindow → list onOverflow → panel state → Pane overflowAbove/Below → paneBorder triangles.
    const { store, inputStores, dispose } = await setup(manyReports(12), true);
    // Start at the top, then move the cursor down a few rows so rows exist BOTH above and below the
    // window — top shows ▴ N and bottom shows ▾ N.
    const { stdin, lastFrame } = render(
      <Harness store={store} inputStores={inputStores} height={8} />,
    );
    await tick();
    stdin.write('j');
    stdin.write('j');
    stdin.write('j');
    await tick();
    const frame = lastFrame() ?? '';
    // A triangle indicator appears in the frame when rows overflow…
    expect(frame).toMatch(/[▴▾]/);
    // …with a count digit alongside it (the dim N in `─ ▴ N ──`).
    expect(frame).toMatch(/[▴▾]\s*\d/);
    // …and the old interior `…` overflow marker is gone (overflow lives in the border now).
    expect(frame).not.toContain('…');
    dispose();
  });

  it('renders empty chrome when the slice has no reports', async () => {
    const { store, inputStores, dispose } = await setup(
      { invalidation_key: 'iv', reports: [] },
      true,
    );
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    expect(lastFrame()).toContain('no reports');
    dispose();
  });
});
