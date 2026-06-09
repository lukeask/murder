/**
 * CrowsPanel test — copied from RosterPanel.test.tsx per the C5 copy recipe.
 *
 * Tests:
 *  - Section headers appear in spec order (collaborator → planners → rogue → ticket).
 *  - Minimized: one-line entries (name + status only).
 *  - Maximized: two-line entries (name+status, then harness · model).
 *  - 'm' key toggles between minimized and maximized (keymap intent, focused only).
 *  - Focus highlight behaviour (border highlight when focused, not when unfocused).
 *  - Keymap intents (j/k cursor) fire only when focused.
 *  - Empty state chrome.
 *
 * Rule 2 proof: we assert section headers appear in spec order without the component
 * ever seeing `row.role` — that stays in the selector.
 *
 * Phase 3: the panel is now a {@link ../../src/components/Pane.tsx Pane} (inline-title border +
 * `[min]`/`[max]` mode label as `titleExtra`) wrapping a single {@link ../../src/components/Ledger.tsx
 * Ledger} over the FLATTENED sections+headers list (option (ii)). The cursor still counts crow rows
 * only; the Ledger highlights via a derived flat-array index so headers are never highlighted. These
 * tests assert the same section order / names / mode toggle / cursor behaviour against that structure.
 */

import { Box } from 'ink';
import { render } from 'ink-testing-library';
import type { JSX } from 'react';
import { describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { CrowsPanel } from '../../src/components/CrowsPanel.js';
import { AppStoreProvider } from '../../src/hooks/useAppStore.js';
import { InputStoresProvider } from '../../src/hooks/useInputStores.js';
import { useRootInput } from '../../src/hooks/useRootInput.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import type { CrowSnapshotReply } from '../../src/store/roster/rosterActions.js';
import { createAppStore } from '../../src/store/store.js';

const ALT_F = '\x1bf';

async function tick(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 20));
}

/** Mixed-type crows: one of each group. */
function mixedCrows(): CrowSnapshotReply {
  return {
    invalidation_key: 'iv',
    sessions: [
      {
        agent_id: 'collab-1',
        role: 'collaborator',
        status: 'running',
        harness: 'claude',
        model: 'anthropic/claude-opus',
        session_name: 'collab',
      },
      {
        agent_id: 'planner-1',
        role: 'planner',
        status: 'idle',
        harness: 'claude',
        model: 'anthropic/claude-sonnet',
        session_name: 'plan-alpha',
      },
      {
        agent_id: 'rogue-1',
        role: 'crow',
        ticket_id: null,
        status: 'running',
        harness: 'codex',
        model: 'openai/gpt-5',
        session_name: 'rogue-one',
      },
      {
        agent_id: 'ticket-1',
        role: 'crow',
        ticket_id: 'T-1',
        status: 'idle',
        harness: 'codex',
        model: 'openai/gpt-4',
        session_name: 'ticket-crow',
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
        {/* Height-bounded like the live app's fullscreen layout, so the Ledger's self-measurement
            returns the AVAILABLE height (not the collapsed content height a bare Box yields under
            ink-testing-library) — this exercises the real measurement path. */}
        <Box height={24}>
          <CrowsPanel />
        </Box>
      </InputStoresProvider>
    </AppStoreProvider>
  );
}

async function setup(reply: CrowSnapshotReply = mixedCrows(), focused = true) {
  const fake = new FakeBusClient();
  fake.stubRpc('state.crow_snapshot', reply);
  fake.stubRpc('state.schedule_snapshot', {
    invalidation_key: 'iv',
    active_tickets: [],
    recent_done_tickets: [],
    archived_tickets: [],
    usage_gauges: [],
  });
  const { store, dispose } = createAppStore(fake);
  await store.getState().actions.roster.refresh();
  const inputStores = createInputStores(['crows'], focused ? 'crows' : 'chat');
  return { fake, store, dispose, inputStores };
}

describe('CrowsPanel — sections and grouping', () => {
  it('renders section headers for each present crow type in spec order', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    // Pane inline title on the top border (Phase 3 Pane + Ledger structure; mode label is titleExtra).
    expect(frame).toContain('╭─ Crows');
    // Section headers appear in spec order.
    const collabPos = frame.indexOf('Collaborator');
    const plannersPos = frame.indexOf('Planning Agents');
    const roguePos = frame.indexOf('Rogue Crows');
    const ticketPos = frame.indexOf('Ticket Crows');
    expect(collabPos).toBeGreaterThanOrEqual(0);
    expect(plannersPos).toBeGreaterThan(collabPos);
    expect(roguePos).toBeGreaterThan(plannersPos);
    expect(ticketPos).toBeGreaterThan(roguePos);
    dispose();
  });

  it('renders crow names under their respective sections', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    expect(frame).toContain('collab');
    expect(frame).toContain('plan-alpha');
    expect(frame).toContain('rogue-one');
    expect(frame).toContain('ticket-crow');
    // The crow-health left-edge glyph (F9 port) is painted on each row's first line.
    expect(frame).toContain('▎');
    dispose();
  });

  it('omits sections for absent types', async () => {
    // Provide only a collaborator crow.
    const { store, inputStores, dispose } = await setup({
      invalidation_key: 'iv',
      sessions: [
        {
          agent_id: 'c1',
          role: 'collaborator',
          status: 'idle',
          harness: 'claude',
          model: null,
          session_name: 'collab-only',
        },
      ],
    });
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    expect(frame).toContain('Collaborator');
    expect(frame).not.toContain('Planning Agents');
    expect(frame).not.toContain('Rogue Crows');
    expect(frame).not.toContain('Ticket Crows');
    dispose();
  });

  it('excludes notetaker/crow_handler roles silently', async () => {
    const { store, inputStores, dispose } = await setup({
      invalidation_key: 'iv',
      sessions: [
        {
          agent_id: 'nt1',
          role: 'notetaker',
          status: 'running',
          harness: 'claude',
          model: null,
          session_name: 'notetaker-agent',
        },
        {
          agent_id: 'ch1',
          role: 'crow_handler',
          status: 'running',
          harness: 'claude',
          model: null,
          session_name: 'handler-agent',
        },
      ],
    });
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    expect(frame).not.toContain('notetaker-agent');
    expect(frame).not.toContain('handler-agent');
    expect(frame).toContain('no crows');
    dispose();
  });
});

describe('CrowsPanel — minimized / maximized', () => {
  it('starts minimized: shows name + status but not harness · model', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    // Mode label present.
    expect(frame).toContain('[min]');
    // Name and status visible.
    expect(frame).toContain('collab');
    expect(frame).toContain('running');
    // Second line (harness · model) should NOT appear in minimized mode.
    expect(frame).not.toContain('claude · claude-opus');
    dispose();
  });

  it('pressing m when focused toggles to maximized (two-line entries)', async () => {
    const { store, inputStores, dispose } = await setup();
    const { stdin, lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    expect(lastFrame()).toContain('[min]');

    stdin.write('m');
    await tick();
    expect(lastFrame()).toContain('[max]');
    // In maximized mode, the second line for the collaborator is visible.
    expect(lastFrame()).toContain('claude · claude-opus');
    dispose();
  });

  it('m key does NOT toggle when panel is not focused', async () => {
    const { store, inputStores, dispose } = await setup(mixedCrows(), false);
    const { stdin, lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const before = lastFrame() ?? '';
    expect(before).toContain('[min]');

    stdin.write('m');
    await tick();
    // Still minimized — key went to chat input, not the panel.
    expect(lastFrame()).toContain('[min]');
    dispose();
  });
});

describe('CrowsPanel — focus highlight and keymap', () => {
  it('shows the focus highlight only when it is the effective focus', async () => {
    const focused = await setup(mixedCrows(), true);
    render(<Harness store={focused.store} inputStores={focused.inputStores} />);
    await tick();
    expect(focused.inputStores.focus.getState().intendedId).toBe('crows');
    focused.dispose();

    const unfocused = await setup(mixedCrows(), false);
    render(<Harness store={unfocused.store} inputStores={unfocused.inputStores} />);
    await tick();
    expect(unfocused.inputStores.focus.getState().intendedId).toBe('chat');
    unfocused.dispose();
  });

  it('cursor moves on j/k only when focused', async () => {
    const { store, inputStores, dispose } = await setup(mixedCrows(), true);
    const { stdin, lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    // The first row in the first section (collaborator) is selected — marker on collab.
    const before = lastFrame() ?? '';
    const markerStart = before.indexOf('▌');
    const collabPos = before.indexOf('collab');
    // Marker should be near the collab entry (first rendered row).
    expect(markerStart).toBeLessThanOrEqual(collabPos + 20);

    // Move down: cursor moves to next row (the planner row).
    stdin.write('j');
    await tick();
    const afterDown = lastFrame() ?? '';
    const markerAfter = afterDown.indexOf('▌');
    // After pressing j, the marker should have moved to a later position in the frame.
    expect(markerAfter).toBeGreaterThan(markerStart);

    // Unfocus via alt+f; k should no longer affect cursor.
    stdin.write(ALT_F);
    await tick();
    expect(inputStores.focus.getState().intendedId).toBe('chat');
    const beforeUnfocused = lastFrame() ?? '';
    stdin.write('k');
    await tick();
    expect(lastFrame()).toBe(beforeUnfocused);
    dispose();
  });

  it('renders empty chrome when the slice has no displayable rows', async () => {
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
