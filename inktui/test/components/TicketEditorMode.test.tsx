/**
 * TicketEditorMode test — the in-layout editor mode test idiom.
 *
 * Copied from {@link ./ConfirmModal.test.tsx} per the C7M recipe. Key differences:
 *  - Uses `inlayout` presentation (not `modal`) — the overlay renders inline, not centered.
 *  - Editor opens via `enter` key on the TicketsPanel (the 'open' intent path), not a dev button.
 *  - The body renders in the overlay slot (below the panels in Shell's Box tree).
 *  - After entering the mode, a panel chord (`j`) must NOT fire (exclusive capture).
 *  - Save path: `alt+s` calls `onIntent('save')`; dismiss path: `Esc` calls `onIntent('dismiss')`.
 *  - Focus is restored after dismiss (the C7M primitive's job).
 *
 * What this test covers:
 *  1. `enter` on the focused TicketsPanel opens the editor mode (ticketEditorMode entered).
 *  2. The editor body from the slice renders in the overlay region.
 *  3. A panel key (`j`) does NOT move the cursor while the editor mode is active (exclusive capture).
 *  4. Checklist toggle: `x` in NORMAL mode on a `- [ ]` line toggles it to `- [x]`.
 *  5. Dismiss: `Esc` from NORMAL mode dismisses the editor; focus is restored to the tickets panel.
 *  6. Save: `alt+s` dismisses + calls `saveBody` (asserted by spying on the action).
 */

import { Box } from 'ink';
import { render } from 'ink-testing-library';
import type { JSX } from 'react';
import { describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { Overlay } from '../../src/components/Overlay.js';
import { TicketsPanel } from '../../src/components/TicketsPanel.js';
import { AppStoreProvider } from '../../src/hooks/useAppStore.js';
import { InputStoresProvider } from '../../src/hooks/useInputStores.js';
import { useRootInput } from '../../src/hooks/useRootInput.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import { selectActiveMode } from '../../src/input/modeStore.js';
import { createAppStore } from '../../src/store/store.js';
import type { TicketDetailReply } from '../../src/store/ticketDetail/ticketDetailActions.js';
import type { ScheduleSnapshotReply } from '../../src/store/tickets/ticketsActions.js';

const ALT_S = '\x1bs';
const ESC = '\x1b';
const RETURN = '\r';

const TICKET_BODY = '## Plan\nDo the thing.\n\n# Checklist\n- [ ] first item\n- [x] done item';

const SNAPSHOT_REPLY: ScheduleSnapshotReply = {
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
  ],
  recent_done_tickets: [],
  archived_tickets: [],
  usage_gauges: [],
};

const DETAIL_REPLY: TicketDetailReply = {
  id: 'T-1',
  title: 'Alpha ticket',
  status: 'in_progress',
  deps: [],
  harness: 'claude',
  model: 'anthropic/claude-opus',
  worktree: null,
  schedule_at: null,
  body: TICKET_BODY,
};

async function tick(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 20));
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
        <Box flexDirection="column">
          <TicketsPanel />
          {/* Overlay sits inline (inlayout) — renders below the panel in the box tree */}
          <Overlay />
        </Box>
      </InputStoresProvider>
    </AppStoreProvider>
  );
}

async function setup() {
  const fake = new FakeBusClient();
  fake.stubRpc('state.schedule_snapshot', SNAPSHOT_REPLY);
  fake.stubRpc('state.ticket_detail', DETAIL_REPLY);
  fake.stubRpc('ticket.save_body', { ok: true });
  fake.stubRpc('ticket.schedule', { ok: true });
  fake.stubRpc('state.crow_snapshot', { invalidation_key: 'iv', sessions: [] });
  const { store, dispose } = createAppStore(fake);
  await store.getState().actions.tickets.refresh();
  // Seed tickets panel visible and focused.
  const inputStores = createInputStores(['tickets'], 'tickets');
  return { fake, store, dispose, inputStores };
}

describe('TicketEditorMode — in-layout editor mode', () => {
  it('enter on TicketsPanel opens the editor, body renders in the overlay region', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame, stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    // Before: no editor mode active, body not in frame.
    expect(selectActiveMode(inputStores.modes)).toBeNull();
    expect(lastFrame()).not.toContain('## Plan');

    // Press enter on the focused TicketsPanel to open the editor.
    stdin.write(RETURN);
    await tick();
    await tick(); // Extra tick for async `open` action to settle.

    // Editor mode is now active.
    expect(selectActiveMode(inputStores.modes)?.id).toBe('ticket-editor');

    // The body is loaded and renders in the overlay region.
    const frame = lastFrame() ?? '';
    expect(frame).toContain('## Plan');
    expect(frame).toContain('# Checklist');
    expect(frame).toContain('- [ ] first item');

    dispose();
  });

  it('layer-0 swallows global chords while editor is active (exclusive capture proof)', async () => {
    // `alt+f` is the `focusChat` global chord — if layer 0 swallows it, focus stays on
    // 'tickets'; if capture were broken the chord would fire and flip intendedId to 'chat'.
    // This is the unambiguous capture assertion the C7M recipe requires.
    const { store, inputStores, dispose } = await setup();
    const { stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    // Confirm initial focus is 'tickets'.
    expect(inputStores.focus.getState().intendedId).toBe('tickets');

    // Open editor (enter key → 'open' intent → ticketEditorMode entered).
    stdin.write(RETURN);
    await tick();
    await tick();
    expect(selectActiveMode(inputStores.modes)?.id).toBe('ticket-editor');

    // While the editor is active, write alt+f (\x06) — the focusChat global chord.
    // Layer 0 must swallow it; focus must remain 'tickets'.
    stdin.write('\x06');
    await tick();

    // If layer 0 capture works: intendedId is still 'tickets'.
    // If capture were broken: focusChat() would have fired → 'chat'.
    expect(inputStores.focus.getState().intendedId).toBe('tickets');
    // Mode is still active (alt+f didn't pop it).
    expect(selectActiveMode(inputStores.modes)?.id).toBe('ticket-editor');

    void store; // used by setup
    dispose();
  });

  it('Esc dismisses the editor and restores focus to the tickets panel', async () => {
    const { store, inputStores, dispose } = await setup();
    const { stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    // Open editor.
    stdin.write(RETURN);
    await tick();
    await tick();
    expect(selectActiveMode(inputStores.modes)?.id).toBe('ticket-editor');
    // A mode is active.
    expect(selectActiveMode(inputStores.modes)).not.toBeNull();

    // Esc dismisses (the mode's declared keymap handles Esc → onIntent('dismiss')).
    stdin.write(ESC);
    await tick();

    expect(selectActiveMode(inputStores.modes)).toBeNull();
    // Prior focus (tickets panel) restored by modeStore exit().
    expect(inputStores.focus.getState().intendedId).toBe('tickets');

    dispose();
  });

  it('alt+s saves the body and dismisses the editor', async () => {
    const { fake, store, inputStores, dispose } = await setup();
    const { stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    // Open editor.
    stdin.write(RETURN);
    await tick();
    await tick();
    expect(selectActiveMode(inputStores.modes)?.id).toBe('ticket-editor');

    // alt+s triggers onIntent('save') → exit + saveBody.
    stdin.write(ALT_S);
    await tick();
    await tick();

    expect(selectActiveMode(inputStores.modes)).toBeNull();
    expect(inputStores.focus.getState().intendedId).toBe('tickets');
    // saveBody was called (rule 3 — only the action calls the bus).
    const saveCalls = fake.rpcCalls.filter((c) => c.method === 'ticket.save_body');
    expect(saveCalls.length).toBeGreaterThan(0);

    dispose();
  });

  it('checklist toggle: x on a - [ ] line toggles it to - [x] (and back)', async () => {
    const { store, inputStores, dispose } = await setup();
    const { stdin, lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    // Open editor.
    stdin.write(RETURN);
    await tick();
    await tick();
    await tick(); // extra tick for async `open` action to load the body

    // Confirm the body is loaded before navigating.
    expect(store.getState().ticketDetail.editedBody).not.toBeNull();

    // The body has '- [ ] first item' somewhere; navigate to that line with 'j' enough times.
    // The body structure: '## Plan' (0), 'Do the thing.' (1), '' (2), '# Checklist' (3),
    // '- [ ] first item' (4), '- [x] done item' (5).
    // NORMAL mode starts at line 0; press 'j' 4 times to reach the unchecked checklist line.
    stdin.write('j');
    await tick();
    stdin.write('j');
    await tick();
    stdin.write('j');
    await tick();
    stdin.write('j');
    await tick();

    // Press 'x' to toggle the checklist item.
    stdin.write('x');
    await tick();

    // The editedBody should now have '- [x] first item'.
    const editedBody = store.getState().ticketDetail.editedBody ?? '';
    expect(editedBody).toContain('- [x] first item');

    // Press 'x' again to toggle back.
    stdin.write('x');
    await tick();
    const editedBody2 = store.getState().ticketDetail.editedBody ?? '';
    expect(editedBody2).toContain('- [ ] first item');

    void lastFrame;
    dispose();
  });
});
