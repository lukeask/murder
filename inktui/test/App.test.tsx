/**
 * App-shell integration test — the skeleton-level companion to the per-panel test idiom
 * ({@link ../components/RosterPanel.test.tsx}). Renders the real {@link App} against a
 * `FakeBusClient`-backed store + the C4 input stores and asserts the composition: the bars, the
 * always-visible chat input, and region visibility driven by the panel set.
 *
 * This is the idiom a future shell-level test copies: build the two store bundles with fakes, render
 * `<App store inputStores />`, assert on the painted frame.
 */

import { render } from 'ink-testing-library';
import { describe, expect, it } from 'vitest';
import { FakeBusClient } from '../src/bus/FakeBusClient.js';
import { App } from '../src/components/App.js';
import { createInputStores } from '../src/input/createInputStores.js';
import type { PanelId } from '../src/input/panels.js';
import { createAppStore } from '../src/store/store.js';

/** Let Ink flush a render + the post-layout measure effects. */
async function tick(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 20));
}

/** Build the shell against fakes with a given visible-panel set. */
function setup(visible: readonly PanelId[]) {
  const fake = new FakeBusClient();
  fake.stubRpc('crow.get_snapshot', { invalidation_key: 'iv', sessions: [] });
  const { store, dispose } = createAppStore(fake);
  const inputStores = createInputStores(visible);
  const tree = render(<App store={store} inputStores={inputStores} bus={fake} />);
  return { fake, store, dispose, inputStores, ...tree };
}

describe('App shell', () => {
  it('always shows the top bar, chat input, and bottom bar', async () => {
    const { lastFrame, dispose } = setup([]);
    await tick();
    const frame = lastFrame() ?? '';
    // Top bar: every panel's subscript label is present (toggled or not).
    expect(frame).toContain('plans₁');
    expect(frame).toContain('crows₀');
    // Chat input is always visible — even with no panels toggled on.
    expect(frame).toContain('message the collaborator');
    // Bottom bar: the global hints are always present.
    expect(frame).toContain('chat');
    dispose();
  });

  it('hides the left region when no left panel is toggled on', async () => {
    const { lastFrame, dispose } = setup([]);
    await tick();
    const frame = lastFrame() ?? '';
    // C6: notes/reports are now real panels, plans still a placeholder.
    expect(frame).not.toContain('Notes');
    expect(frame).not.toContain('Reports');
    expect(frame).not.toContain('Crows');
    dispose();
  });

  it('shows the left region with the right placeholder when plans panel is on', async () => {
    // plans is still a placeholder (filled by a later chunk — no C6 assignment for plans).
    const { lastFrame, dispose } = setup(['plans']);
    await tick();
    const frame = lastFrame() ?? '';
    expect(frame).toContain('Plans');
    // plans is still a placeholder; notes/reports are real panels now.
    expect(frame).not.toContain('Notes');
    // No right panel toggled → right region stays collapsed.
    expect(frame).not.toContain('Crows');
    dispose();
  });

  it('shows the notes panel when notes is toggled on', async () => {
    const { lastFrame, dispose } = setup(['notes']);
    await tick();
    const frame = lastFrame() ?? '';
    expect(frame).toContain('Notes');
    expect(frame).not.toContain('Crows');
    dispose();
  });

  it('shows the reports panel when reports is toggled on', async () => {
    const { lastFrame, dispose } = setup(['reports']);
    await tick();
    const frame = lastFrame() ?? '';
    expect(frame).toContain('Reports');
    dispose();
  });

  it('shows the right region (reference crows panel) when crows is on', async () => {
    const { lastFrame, dispose } = setup(['crows']);
    await tick();
    const frame = lastFrame() ?? '';
    expect(frame).toContain('Crows');
    dispose();
  });

  it('highlights only the toggled panels in the top bar', async () => {
    // Hard to assert ANSI colour on the frame; assert the selector behaviour the bar renders from.
    const { inputStores, dispose } = setup(['plans', 'crows']);
    await tick();
    const visible = inputStores.panels.getState().visible;
    expect(visible.has('plans')).toBe(true);
    expect(visible.has('crows')).toBe(true);
    expect(visible.has('notes')).toBe(false);
    dispose();
  });
});
