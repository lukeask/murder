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
import { App, deriveSpawnContext } from '../src/components/App.js';
import { TMUX_MODE_ID, tmuxMode } from '../src/components/TmuxMode.js';
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

  it('fullscreen tmux mode suppresses bars and panels (presentationHidesLayout)', async () => {
    // Render with 'crows' visible so the top bar label is present before entering the mode.
    const { lastFrame, inputStores, dispose } = setup(['crows']);
    await tick();

    // Before: the normal layout is showing — crows₀ label is present in the top bar.
    expect(lastFrame()).toContain('crows₀');

    // Enter fullscreen tmux mode via the store (same path useRootInput.ts toggleTmux takes).
    inputStores.modes.getState().enter(tmuxMode(inputStores.modes));
    await tick();

    // After: Shell saw presentationHidesLayout → returned only <Overlay /> (the TmuxFrame surface).
    // The top-bar chrome (crows₀) must be gone, and the waiting placeholder must be present.
    expect(lastFrame()).not.toContain('crows₀');
    expect(lastFrame()).toContain('waiting');

    // Exit and verify the layout is restored.
    inputStores.modes.getState().exit(TMUX_MODE_ID);
    await tick();
    expect(lastFrame()).toContain('crows₀');

    dispose();
  });
});

describe('deriveSpawnContext', () => {
  /** Build a minimal FakeBusClient store + input stores with the given focus. */
  function makeStores(focusId: PanelId | 'chat') {
    const fake = new FakeBusClient();
    fake.stubRpc('crow.get_snapshot', { invalidation_key: 'iv', sessions: [] });
    const { store, dispose } = createAppStore(fake);
    // Pass focusId as the initial focus; visible panels include the focused panel (except 'chat').
    const panels: PanelId[] = focusId === 'chat' ? [] : [focusId as PanelId];
    const inputStores = createInputStores(panels, focusId === 'chat' ? 'chat' : focusId);
    return { store, dispose, focus: inputStores.focus };
  }

  it('returns null when notes is focused but has no rows', () => {
    const { store, focus, dispose } = makeStores('notes');
    const result = deriveSpawnContext(focus, store);
    expect(result).toBeNull();
    dispose();
  });

  it('returns SpawnContext for first notes row when notes is focused', () => {
    const { store, focus, dispose } = makeStores('notes');
    // Seed the notes slice directly via setState.
    store.setState((s) => ({
      ...s,
      notes: { ...s.notes, rows: [{ name: 'my-note', charCount: 100, updatedAt: '2026-01-01' }] },
    }));
    const result = deriveSpawnContext(focus, store);
    expect(result).toEqual({ title: 'my-note', path: '.murder/notes/my-note.md' });
    dispose();
  });

  it('returns null when reports is focused but has no rows', () => {
    const { store, focus, dispose } = makeStores('reports');
    const result = deriveSpawnContext(focus, store);
    expect(result).toBeNull();
    dispose();
  });

  it('returns SpawnContext for first reports row when reports is focused', () => {
    const { store, focus, dispose } = makeStores('reports');
    store.setState((s) => ({
      ...s,
      reports: {
        ...s.reports,
        rows: [{ name: 'my-report', charCount: 200, updatedAt: '2026-01-01' }],
      },
    }));
    const result = deriveSpawnContext(focus, store);
    expect(result).toEqual({ title: 'my-report', path: '.murder/reports/my-report.md' });
    dispose();
  });

  it('returns null when a non-doc panel (tickets) is focused', () => {
    const { store, focus, dispose } = makeStores('tickets');
    const result = deriveSpawnContext(focus, store);
    expect(result).toBeNull();
    dispose();
  });

  it('returns null when chat is focused', () => {
    const { store, focus, dispose } = makeStores('chat');
    const result = deriveSpawnContext(focus, store);
    expect(result).toBeNull();
    dispose();
  });
});
