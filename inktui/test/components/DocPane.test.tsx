/**
 * DocPane test — the read-only doc viewer as a focusable **Stage pane** (Phase 4b; was the retired
 * in-layout `docViewMode`).
 *
 * The doc-view is no longer a mode: opening a plan/note/report renders a {@link StageDocPane} on the
 * {@link Stage} (a focusable `stage:doc:<name>` pane), NOT a mode pushed onto the mode stack. So these
 * tests assert the Stage-pane model instead of the old `selectActiveMode(...).id === DOC_VIEW_MODE_ID`:
 *  1. `enter` on a focused plan row opens the doc → a doc Pane renders in the Stage (its inline title
 *     is the `.murder/<dir>/<name>.md` path; the fetched body shows) and NO mode is entered.
 *  2. The doc pane is focusable: opening focuses `stage:doc:<name>` (its rect registers, the pane
 *     holds effective focus), and `j`/`k` scroll its body window.
 *  3. `enter` / `esc` on the shown doc closes it (the `docView` slice clears) and focus re-homes to
 *     **chat** — the derived re-home invariant (the doc pane unmounts → its rect drops → resolveFocus
 *     falls home to chat). This is the accepted behaviour change from the old mode (which restored the
 *     originating list focus); a Stage pane re-homes to chat exactly like a hidden panel.
 *  4. `enter` on the already-open doc (toggle) closes it.
 *  5. The open doc is the spawn wizard's focused-doc (asserted via the `docView` slice the wizard reads).
 */

import { Box } from 'ink';
import { render } from 'ink-testing-library';
import type { JSX } from 'react';
import { describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { computeDocWindow, computeScrollThumb } from '../../src/components/DocPane.js';
import { PlansPanel } from '../../src/components/PlansPanel.js';
import { Stage } from '../../src/components/Stage.js';
import { AppStoreProvider } from '../../src/hooks/useAppStore.js';
import { InputStoresProvider } from '../../src/hooks/useInputStores.js';
import { useRootInput } from '../../src/hooks/useRootInput.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import { CHAT_FOCUS, selectEffectiveFocus } from '../../src/input/focusStore.js';
import { selectActiveMode } from '../../src/input/modeStore.js';
import { createAppStore } from '../../src/store/store.js';

const RETURN = '\r';
const ESC = '\x1b';
// A body long enough that `j` scrolls a visible line off the top. Under ink-testing-library's sizeless
// render `measureElement` reports 0, so the window falls back to FALLBACK_HEIGHT (14) — line-0 sits at
// the top of that window and scrolls off on the first `j`.
const DOC_BODY = Array.from({ length: 30 }, (_, i) => `line-${i}`).join('\n');

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
          <Stage />
        </Box>
      </InputStoresProvider>
    </AppStoreProvider>
  );
}

async function setup() {
  const fake = new FakeBusClient();
  fake.stubRpc('state.plans_snapshot', {
    invalidation_key: 'iv',
    plans: [{ name: 'my-plan', char_count: 100, updated_at: '2026-06-01T00:00:00', parent: null }],
  });
  fake.stubRpc('state.plan_display', { name: 'my-plan', markdown: DOC_BODY });
  fake.stubRpc('state.crow_snapshot', { invalidation_key: 'iv', sessions: [] });
  const { store, dispose } = createAppStore(fake);
  await store.getState().actions.plans.refresh();
  const inputStores = createInputStores(['plans'], 'plans');
  return { fake, store, dispose, inputStores };
}

describe('computeDocWindow — window math (the test seam)', () => {
  it('returns the full body when it fits the height', () => {
    expect(computeDocWindow(5, 0, 14)).toEqual({ start: 0, end: 14, maxScroll: 0 });
  });

  it('windows a tall body and exposes maxScroll', () => {
    // 30 lines, 14-row window → can scroll to 16 (30 - 14).
    expect(computeDocWindow(30, 0, 14)).toEqual({ start: 0, end: 14, maxScroll: 16 });
    expect(computeDocWindow(30, 5, 14)).toEqual({ start: 5, end: 19, maxScroll: 16 });
  });

  it('clamps an over-scrolled offset to maxScroll (short body cannot strand the window)', () => {
    expect(computeDocWindow(30, 999, 14)).toEqual({ start: 16, end: 30, maxScroll: 16 });
  });

  it('clamps a negative offset to 0', () => {
    expect(computeDocWindow(30, -5, 14)).toEqual({ start: 0, end: 14, maxScroll: 16 });
  });

  it('treats a non-positive height as at least 1 row', () => {
    expect(computeDocWindow(30, 0, 0)).toEqual({ start: 0, end: 1, maxScroll: 29 });
  });
});

describe('computeScrollThumb — scrollbar geometry', () => {
  it('returns null when the content fits (no scrollbar drawn)', () => {
    expect(computeScrollThumb(10, 0, 14)).toBeNull();
    expect(computeScrollThumb(14, 0, 14)).toBeNull();
  });

  it('sits the thumb at the top when scrolled to 0', () => {
    expect(computeScrollThumb(30, 0, 14)?.offset).toBe(0);
  });

  it('sits the thumb at the bottom when scrolled to the end', () => {
    const thumb = computeScrollThumb(30, 16, 14);
    expect(thumb).not.toBeNull();
    // offset + size reaches the track bottom (height).
    expect(
      (thumb as { size: number; offset: number }).offset + (thumb as { size: number }).size,
    ).toBe(14);
  });

  it('sizes the thumb to the visible fraction (min 1 cell)', () => {
    // h*h/total = 14*14/30 ≈ 6.53 → 7.
    expect(computeScrollThumb(30, 0, 14)?.size).toBe(7);
    // A very long body still gets a 1-cell thumb.
    expect(computeScrollThumb(10000, 0, 14)?.size).toBe(1);
  });

  it('never overruns the track', () => {
    for (let scroll = 0; scroll <= 16; scroll++) {
      const thumb = computeScrollThumb(30, scroll, 14);
      if (thumb === null) {
        continue;
      }
      expect(thumb.offset).toBeGreaterThanOrEqual(0);
      expect(thumb.offset + thumb.size).toBeLessThanOrEqual(14);
    }
  });
});

describe('DocPane — open / scroll / close as a Stage pane', () => {
  it('enter opens the doc as a Stage pane (path title + body), NOT a mode', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame, stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    expect(selectActiveMode(inputStores.modes)).toBeNull();

    stdin.write(RETURN);
    await tick();
    await tick(); // async state.plan_display settles

    // Still no mode — the doc is a Stage pane now.
    expect(selectActiveMode(inputStores.modes)).toBeNull();
    const frame = lastFrame() ?? '';
    expect(frame).toContain('.murder/plans/my-plan.md');
    expect(frame).toContain('line-0');
    expect(store.getState().docView.open).toEqual({ kind: 'plan', name: 'my-plan' });
    dispose();
  });

  it('opening focuses the doc pane (stage:doc:<name>) and j/k scroll its body window', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame, stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    stdin.write(RETURN); // open
    await tick();
    await tick();

    // Focus intent points at the doc pane, which holds the effective focus (its rect registered).
    expect(inputStores.focus.getState().intendedId).toBe('stage:doc:my-plan');
    expect(inputStores.focus.getState().rects.has('stage:doc:my-plan')).toBe(true);

    expect(lastFrame() ?? '').toContain('line-0');
    stdin.write('j'); // scroll down one line
    await tick();
    // The top line scrolled off; line-1 is now the first body line.
    expect(lastFrame() ?? '').not.toContain('line-0');
    expect(lastFrame() ?? '').toContain('line-1');

    stdin.write('k'); // scroll back up
    await tick();
    expect(lastFrame() ?? '').toContain('line-0');
    dispose();
  });

  it('enter on the shown doc closes it (slice cleared) and re-homes focus to chat', async () => {
    const { store, inputStores, dispose } = await setup();
    const { stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    stdin.write(RETURN); // open
    await tick();
    await tick();
    expect(store.getState().docView.open).not.toBeNull();

    stdin.write(RETURN); // close (enter on the focused doc pane)
    await tick();
    expect(store.getState().docView.open).toBeNull();
    // The doc pane unmounted → its rect dropped → the EFFECTIVE focus re-homes to chat (NOT back to
    // 'plans'). The re-home is derived: `intendedId` may still literally name the closed doc, but
    // `resolveFocus` (selectEffectiveFocus) collapses an unmounted Stage pane to chat — the invariant.
    expect(selectEffectiveFocus(inputStores.focus)).toBe(CHAT_FOCUS);
    dispose();
  });

  it('esc on the shown doc closes it too', async () => {
    const { store, inputStores, dispose } = await setup();
    const { stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    stdin.write(RETURN); // open
    await tick();
    await tick();
    expect(store.getState().docView.open).not.toBeNull();

    stdin.write(ESC); // close
    await tick();
    expect(store.getState().docView.open).toBeNull();
    dispose();
  });

  it('enter on the already-open doc (from the panel) toggles it closed', async () => {
    const { store, inputStores, dispose } = await setup();
    const { stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    stdin.write(RETURN); // open
    await tick();
    await tick();
    expect(store.getState().docView.open).toEqual({ kind: 'plan', name: 'my-plan' });

    // Re-focus the plans panel so the panel's `enter → open` fires the toggle (not the doc pane's
    // `enter → close`). Both close the slice; this exercises the useDocView toggle branch.
    inputStores.focus.getState().focus('plans');
    await tick();
    stdin.write(RETURN); // panel enter on the already-open doc → toggle closed
    await tick();
    expect(store.getState().docView.open).toBeNull();
    dispose();
  });

  it('the open doc is the spawn wizard focused-doc (the docView slice the wizard reads)', async () => {
    const { store, inputStores, dispose } = await setup();
    const { stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    stdin.write(RETURN); // open
    await tick();
    await tick();
    expect(store.getState().docView.open).toEqual({ kind: 'plan', name: 'my-plan' });
    dispose();
  });
});
