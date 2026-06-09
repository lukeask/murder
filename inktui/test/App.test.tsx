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
  fake.stubRpc('state.crow_snapshot', { invalidation_key: 'iv', sessions: [] });
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
    expect(frame).toContain('type a message');
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

  it('C10: CrowChatPanel mounts below CrowsPanel when crows panel is on + favorited crows exist', async () => {
    // App-path test: renders the full App (not just the component harness) and proves that
    // CrowChatPanel is actually mounted — not just that it renders in isolation.
    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', {
      invalidation_key: 'iv',
      sessions: [
        { agent_id: 'collab-1', role: 'collaborator', status: 'idle', session_name: 'TestCollab' },
      ],
    });
    // F2: chat sends route through command.submit (agent.message command kind), not a direct RPC.
    fake.stubRpc('command.submit', { ok: true, command_id: 'cmd-1' });
    fake.stubRpc('command.status', { ok: true, status: 'done', result_json: '{}' });
    const { store, dispose } = createAppStore(fake);
    await store.getState().actions.roster.refresh();

    const inputStores = createInputStores(['crows']);
    const { lastFrame } = render(<App store={store} inputStores={inputStores} bus={fake} />);
    await tick();

    const frame = lastFrame() ?? '';
    // CrowsPanel header is present (confirms crows region rendered).
    expect(frame).toContain('Crows');
    // CrowChatPanel shows a pane for the collaborator (proves it was mounted under App).
    expect(frame).toContain('TestCollab');
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

describe('deriveSpawnContext — focused doc = the open doc-view (C11)', () => {
  /** Build a minimal FakeBusClient-backed store. */
  function makeStore() {
    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', { invalidation_key: 'iv', sessions: [] });
    return createAppStore(fake);
  }

  it('returns null when no doc is open (the closed doc-view default)', () => {
    const { store, dispose } = makeStore();
    expect(deriveSpawnContext(store)).toBeNull();
    dispose();
  });

  it('returns the open note as the focused doc (reference-by-path)', () => {
    const { store, dispose } = makeStore();
    store.setState((s) => ({
      docView: { ...s.docView, open: { kind: 'note', name: 'my-note' } },
    }));
    expect(deriveSpawnContext(store)).toEqual({
      title: 'my-note',
      path: '.murder/notes/my-note.md',
    });
    dispose();
  });

  it('returns the open report as the focused doc', () => {
    const { store, dispose } = makeStore();
    store.setState((s) => ({
      docView: { ...s.docView, open: { kind: 'report', name: 'my-report' } },
    }));
    expect(deriveSpawnContext(store)).toEqual({
      title: 'my-report',
      path: '.murder/reports/my-report.md',
    });
    dispose();
  });

  it('returns the open plan as the focused doc', () => {
    const { store, dispose } = makeStore();
    store.setState((s) => ({
      docView: { ...s.docView, open: { kind: 'plan', name: 'my-plan' } },
    }));
    expect(deriveSpawnContext(store)).toEqual({
      title: 'my-plan',
      path: '.murder/plans/my-plan.md',
    });
    dispose();
  });
});
