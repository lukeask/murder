/**
 * `usePaneUiClampedCursor` tests — the store-backed variant of {@link useClampedCursor}, whose
 * selection index lives in {@link paneUiStore} keyed by pane id.
 *
 * Covers:
 *  - clamps into `[0, rowCount-1]` on read;
 *  - `moveDown` / `moveUp` step and clamp at the ends;
 *  - `setCursor` accepts a value and a functional updater;
 *  - the cursor **survives remount** (the whole point of hoisting it out of `useState`);
 *  - distinct ids are independent.
 */

import { Text } from 'ink';
import { render } from 'ink-testing-library';
import { act, type JSX } from 'react';
import { describe, expect, it } from 'vitest';
import {
  type ClampedCursor,
  usePaneUiClampedCursor,
} from '../../../src/components/panes/shared/useClampedCursor.js';
import { InputStoresProvider } from '../../../src/hooks/useInputStores.js';
import { createInputStores } from '../../../src/input/createInputStores.js';

async function flushReact(): Promise<void> {
  await act(async () => {});
}

/** Renders the hook, exposing its returned api through `capture` so the test can drive it. */
function Probe({
  id,
  rowCount,
  capture,
}: {
  readonly id: string;
  readonly rowCount: number;
  readonly capture: (api: ClampedCursor) => void;
}): JSX.Element {
  const api = usePaneUiClampedCursor(id, rowCount);
  capture(api);
  return <Text>cursor:{api.cursor}</Text>;
}

describe('usePaneUiClampedCursor', () => {
  it('starts at 0, steps with moveDown/moveUp, and clamps at both ends', async () => {
    const inputStores = createInputStores();
    let api!: ClampedCursor;
    const tree = render(
      <InputStoresProvider value={inputStores}>
        <Probe id="plans" rowCount={3} capture={(a) => (api = a)} />
      </InputStoresProvider>,
    );
    await flushReact();
    expect(tree.lastFrame()).toContain('cursor:0');

    await act(async () => api.moveDown());
    expect(tree.lastFrame()).toContain('cursor:1');

    await act(async () => {
      api.moveDown();
      api.moveDown();
    });
    // rowCount 3 ⇒ max index 2; further moveDown clamps.
    expect(tree.lastFrame()).toContain('cursor:2');

    await act(async () => api.moveUp());
    expect(tree.lastFrame()).toContain('cursor:1');

    tree.unmount();
  });

  it('setCursor accepts a value and a functional updater', async () => {
    const inputStores = createInputStores();
    let api!: ClampedCursor;
    const tree = render(
      <InputStoresProvider value={inputStores}>
        <Probe id="reports" rowCount={10} capture={(a) => (api = a)} />
      </InputStoresProvider>,
    );
    await flushReact();

    await act(async () => api.setCursor(4));
    expect(tree.lastFrame()).toContain('cursor:4');

    await act(async () => api.setCursor((c) => c + 2));
    expect(tree.lastFrame()).toContain('cursor:6');

    tree.unmount();
  });

  it('clamps a stored value down when rowCount shrinks', async () => {
    const inputStores = createInputStores();
    inputStores.paneUi.getState().setCursor('plans', 9);
    const tree = render(
      <InputStoresProvider value={inputStores}>
        <Probe id="plans" rowCount={3} capture={() => {}} />
      </InputStoresProvider>,
    );
    await flushReact();
    // Stored 9, live rowCount 3 ⇒ clamped to 2 on read.
    expect(tree.lastFrame()).toContain('cursor:2');

    tree.unmount();
  });

  it('survives remount — the cursor is hoisted into the store, not component state', async () => {
    const inputStores = createInputStores();
    let api!: ClampedCursor;
    const first = render(
      <InputStoresProvider value={inputStores}>
        <Probe id="plans" rowCount={10} capture={(a) => (api = a)} />
      </InputStoresProvider>,
    );
    await flushReact();
    await act(async () => api.setCursor(5));
    first.unmount();

    const second = render(
      <InputStoresProvider value={inputStores}>
        <Probe id="plans" rowCount={10} capture={(a) => (api = a)} />
      </InputStoresProvider>,
    );
    await flushReact();
    expect(second.lastFrame()).toContain('cursor:5');

    second.unmount();
  });

  it('keeps distinct ids independent', async () => {
    const inputStores = createInputStores();
    let plansApi!: ClampedCursor;
    let reportsApi!: ClampedCursor;
    const tree = render(
      <InputStoresProvider value={inputStores}>
        <Probe id="plans" rowCount={10} capture={(a) => (plansApi = a)} />
        <Probe id="reports" rowCount={10} capture={(a) => (reportsApi = a)} />
      </InputStoresProvider>,
    );
    await flushReact();
    await act(async () => {
      plansApi.setCursor(3);
      reportsApi.setCursor(7);
    });
    expect(inputStores.paneUi.getState().cursors).toEqual({ plans: 3, reports: 7 });

    tree.unmount();
  });
});
