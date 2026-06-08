/**
 * NotesPanel test — copied from {@link ./RosterPanel.test.tsx} per the C5 idiom.
 *
 * Recipe summary (same as the reference, with notes-specific stubs):
 *  1. Build `FakeBusClient`, stub `note.get_snapshot`, build the store.
 *  2. Build C4 input stores, seeding `notes` visible and optionally focused.
 *  3. Render inside both providers + `useRootInput`.
 *  4. Assert two-line rows, focus highlight, keymap intent only when focused.
 */

import { Box } from 'ink';
import { render } from 'ink-testing-library';
import type { JSX } from 'react';
import { describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { NotesPanel } from '../../src/components/NotesPanel.js';
import { AppStoreProvider } from '../../src/hooks/useAppStore.js';
import { InputStoresProvider } from '../../src/hooks/useInputStores.js';
import { useRootInput } from '../../src/hooks/useRootInput.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import type { NotesSnapshotReply } from '../../src/store/notes/notesActions.js';
import { createAppStore } from '../../src/store/store.js';

const CTRL_F = '\x06';

async function tick(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 20));
}

function twoNotes(): NotesSnapshotReply {
  return {
    invalidation_key: 'iv',
    notes: [
      {
        name: 'alpha-note',
        char_count: 1234,
        updated_at: '2026-06-08T10:00:00',
      },
      {
        name: 'bravo-note',
        char_count: 567,
        updated_at: '2026-06-01T08:00:00',
      },
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
        <Box>
          <NotesPanel />
        </Box>
      </InputStoresProvider>
    </AppStoreProvider>
  );
}

function RootInput(): null {
  useRootInput();
  return null;
}

async function setup(reply: NotesSnapshotReply = twoNotes(), focused = true) {
  const fake = new FakeBusClient();
  fake.stubRpc('note.get_snapshot', reply);
  // Also stub crow.get_snapshot so createAppStore doesn't choke on any stray event.
  fake.stubRpc('crow.get_snapshot', { invalidation_key: 'iv', sessions: [] });
  const { store, dispose } = createAppStore(fake);
  await store.getState().actions.notes.refresh();
  const inputStores = createInputStores(['notes'], focused ? 'notes' : 'chat');
  return { fake, store, dispose, inputStores };
}

describe('NotesPanel', () => {
  it('renders two-line entries (name, then charCount · updatedAt)', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    // Line 1: name. Line 2: char count and formatted date.
    expect(frame).toContain('alpha-note');
    expect(frame).toContain('2026-06-08 10:00');
    expect(frame).toContain('bravo-note');
    expect(frame).toContain('2026-06-01 08:00');
    dispose();
  });

  it('shows the focus highlight only when it is the effective focus', async () => {
    const focusedSetup = await setup(twoNotes(), true);
    render(<Harness store={focusedSetup.store} inputStores={focusedSetup.inputStores} />);
    await tick();
    expect(focusedSetup.inputStores.focus.getState().intendedId).toBe('notes');
    focusedSetup.dispose();

    const unfocusedSetup = await setup(twoNotes(), false);
    render(<Harness store={unfocusedSetup.store} inputStores={unfocusedSetup.inputStores} />);
    await tick();
    expect(unfocusedSetup.inputStores.focus.getState().intendedId).toBe('chat');
    unfocusedSetup.dispose();
  });

  it('moves the local cursor on a declared key only when focused', async () => {
    const { store, inputStores, dispose } = await setup(twoNotes(), true);
    const { stdin, lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    // Focused: 'j' fires cursorDown → cursor marker moves below alpha-note.
    const before = lastFrame() ?? '';
    expect(before.indexOf('▌')).toBeLessThan(before.indexOf('bravo-note'));
    stdin.write('j');
    await tick();
    const afterDown = lastFrame() ?? '';
    expect(afterDown.indexOf('▌')).toBeGreaterThan(afterDown.indexOf('alpha-note'));

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

  it('renders empty chrome when the slice has no notes', async () => {
    const { store, inputStores, dispose } = await setup(
      { invalidation_key: 'iv', notes: [] },
      true,
    );
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    expect(lastFrame()).toContain('no notes');
    dispose();
  });
});
