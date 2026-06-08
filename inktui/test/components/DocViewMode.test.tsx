/**
 * DocViewMode test — the read-only in-layout doc-view mode (C11, part D).
 *
 * Mirrors {@link ./TicketEditorMode.test.tsx}'s in-layout-mode idiom. Covers the full toggle cycle
 * the spec requires:
 *  1. `enter` on a focused plan/note/report row opens the doc view (mode entered, body fetched +
 *     rendered in the overlay region).
 *  2. `enter` on the shown doc minimises it (mode exits, docView slice closed) and focus is restored
 *     to the originating list (the C7M primitive's job).
 *  3. `enter` again restores (re-opens) the doc.
 *  4. While the doc view is up, a global chord (`ctrl+f`) is swallowed by layer 0 — exclusive capture.
 *  5. The open doc is the spawn wizard's focused-doc (asserted via the docView slice the wizard reads).
 */

import { Box } from 'ink';
import { render } from 'ink-testing-library';
import type { JSX } from 'react';
import { describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { DOC_VIEW_MODE_ID } from '../../src/components/DocViewMode.js';
import { Overlay } from '../../src/components/Overlay.js';
import { PlansPanel } from '../../src/components/PlansPanel.js';
import { AppStoreProvider } from '../../src/hooks/useAppStore.js';
import { InputStoresProvider } from '../../src/hooks/useInputStores.js';
import { useRootInput } from '../../src/hooks/useRootInput.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import { selectActiveMode } from '../../src/input/modeStore.js';
import { createAppStore } from '../../src/store/store.js';

const RETURN = '\r';
const DOC_BODY = '# My Plan\nline two\nline three';

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
          <PlansPanel />
          <Overlay />
        </Box>
      </InputStoresProvider>
    </AppStoreProvider>
  );
}

async function setup() {
  const fake = new FakeBusClient();
  fake.stubRpc('plan.get_snapshot', {
    invalidation_key: 'iv',
    plans: [{ name: 'my-plan', char_count: 100, updated_at: '2026-06-01T00:00:00', parent: null }],
  });
  fake.stubRpc('doc.get', { body: DOC_BODY });
  fake.stubRpc('crow.get_snapshot', { invalidation_key: 'iv', sessions: [] });
  const { store, dispose } = createAppStore(fake);
  await store.getState().actions.plans.refresh();
  const inputStores = createInputStores(['plans'], 'plans');
  return { fake, store, dispose, inputStores };
}

describe('DocViewMode — open / minimize / restore + focus', () => {
  it('enter opens the doc view; body fetched + rendered in the overlay', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame, stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    expect(selectActiveMode(inputStores.modes)).toBeNull();

    stdin.write(RETURN);
    await tick();
    await tick(); // async doc.get settles

    expect(selectActiveMode(inputStores.modes)?.id).toBe(DOC_VIEW_MODE_ID);
    const frame = lastFrame() ?? '';
    expect(frame).toContain('.murder/plans/my-plan.md');
    expect(frame).toContain('# My Plan');
    expect(store.getState().docView.open).toEqual({ kind: 'plan', name: 'my-plan' });
    dispose();
  });

  it('enter on the shown doc minimises it, closes the slice, and restores focus to the list', async () => {
    const { store, inputStores, dispose } = await setup();
    const { stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    expect(inputStores.focus.getState().intendedId).toBe('plans');

    // Open.
    stdin.write(RETURN);
    await tick();
    await tick();
    expect(selectActiveMode(inputStores.modes)?.id).toBe(DOC_VIEW_MODE_ID);

    // Minimise (enter while shown → the mode's `close` intent).
    stdin.write(RETURN);
    await tick();
    expect(selectActiveMode(inputStores.modes)).toBeNull();
    expect(store.getState().docView.open).toBeNull();
    // Focus restored to the originating list (the C7M primitive).
    expect(inputStores.focus.getState().intendedId).toBe('plans');
    dispose();
  });

  it('enter again restores (re-opens) the doc after minimising', async () => {
    const { store, inputStores, dispose } = await setup();
    const { stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    stdin.write(RETURN); // open
    await tick();
    await tick();
    stdin.write(RETURN); // minimise
    await tick();
    expect(selectActiveMode(inputStores.modes)).toBeNull();

    stdin.write(RETURN); // re-open
    await tick();
    await tick();
    expect(selectActiveMode(inputStores.modes)?.id).toBe(DOC_VIEW_MODE_ID);
    expect(store.getState().docView.open).toEqual({ kind: 'plan', name: 'my-plan' });
    dispose();
  });

  it('layer-0 swallows a global chord while the doc view is up (exclusive capture)', async () => {
    const { store, inputStores, dispose } = await setup();
    const { stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    stdin.write(RETURN); // open
    await tick();
    await tick();
    expect(selectActiveMode(inputStores.modes)?.id).toBe(DOC_VIEW_MODE_ID);

    stdin.write('\x06'); // ctrl+f (focusChat global chord)
    await tick();
    // Captured by layer 0 → focus stays 'plans', mode still up.
    expect(inputStores.focus.getState().intendedId).toBe('plans');
    expect(selectActiveMode(inputStores.modes)?.id).toBe(DOC_VIEW_MODE_ID);
    void store;
    dispose();
  });
});
