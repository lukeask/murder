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
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { FakeBusClient } from '../src/bus/FakeBusClient.js';
import { App, deriveSpawnContext } from '../src/components/App.js';
import { TMUX_MODE_ID, tmuxMode } from '../src/components/TmuxMode.js';
import { createInputStores } from '../src/input/createInputStores.js';
import { CHAT_FOCUS, selectEffectiveFocus } from '../src/input/focusStore.js';
import type { PanelId } from '../src/input/panels.js';
import { createAppStore } from '../src/store/store.js';
import { DEFAULT_THEME_ID } from '../src/theme/palettes.js';
import { themeStore } from '../src/theme/themeStore.js';

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

  it('Phase 4a: the Stage mounts a chat pane for a favorited crow (center region, not the crows Rail)', async () => {
    // App-path test: renders the full App (not just the component harness) and proves the favorited
    // crow's chat-history pane is mounted in the center Stage. (Pre-4a this lived in CrowChatPanel
    // stacked under CrowsPanel in the right Rail; 4a moved it into the always-mounted Stage.)
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
    // The Stage shows a pane titled for the collaborator (proves it was mounted under App's center).
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

describe('deriveSpawnContext — doc file context gated by the highlighted Stage pane (stagelayout)', () => {
  /** Build a minimal FakeBusClient-backed store. */
  function makeStore() {
    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', { invalidation_key: 'iv', sessions: [] });
    return createAppStore(fake);
  }

  it('returns null when no doc is open (the closed doc-view default)', () => {
    const { store, dispose } = makeStore();
    expect(deriveSpawnContext(store, 'stage:doc:anything')).toBeNull();
    dispose();
  });

  it('returns the open note when the doc pane is highlighted (reference-by-path)', () => {
    const { store, dispose } = makeStore();
    store.setState((s) => ({
      docView: { ...s.docView, open: { kind: 'note', name: 'my-note' } },
    }));
    expect(deriveSpawnContext(store, 'stage:doc:my-note')).toEqual({
      title: 'my-note',
      path: '.murder/notes/my-note.md',
    });
    dispose();
  });

  it('returns the open report when the doc pane is highlighted', () => {
    const { store, dispose } = makeStore();
    store.setState((s) => ({
      docView: { ...s.docView, open: { kind: 'report', name: 'my-report' } },
    }));
    expect(deriveSpawnContext(store, 'stage:doc:my-report')).toEqual({
      title: 'my-report',
      path: '.murder/reports/my-report.md',
    });
    dispose();
  });

  it('returns the open plan when the doc pane is highlighted', () => {
    const { store, dispose } = makeStore();
    store.setState((s) => ({
      docView: { ...s.docView, open: { kind: 'plan', name: 'my-plan' } },
    }));
    expect(deriveSpawnContext(store, 'stage:doc:my-plan')).toEqual({
      title: 'my-plan',
      path: '.murder/plans/my-plan.md',
    });
    dispose();
  });

  it('returns null when a CHAT pane is highlighted, even though a doc is open elsewhere', () => {
    // stagelayout requirement: a highlighted chat-history pane gets NO file prompt, even if a doc
    // happens to be open on the Stage — the file context follows the highlight, not the open slice.
    const { store, dispose } = makeStore();
    store.setState((s) => ({
      docView: { ...s.docView, open: { kind: 'plan', name: 'my-plan' } },
    }));
    expect(deriveSpawnContext(store, 'stage:chat:crow-1')).toBeNull();
    dispose();
  });

  it('returns null when the chat input is focused (no Stage pane highlighted)', () => {
    const { store, dispose } = makeStore();
    store.setState((s) => ({
      docView: { ...s.docView, open: { kind: 'plan', name: 'my-plan' } },
    }));
    expect(deriveSpawnContext(store, CHAT_FOCUS)).toBeNull();
    dispose();
  });
});

describe('ctrl+q — close the highlighted Stage pane (stagelayout)', () => {
  // ctrl+q rides the clean legacy byte 0x11, which Ink reports as `{ ctrl: true, input: 'q' }`.
  const CTRL_Q = '\x11';

  /** A full App against fakes, with a roster carrying one default-favorited collaborator (→ one Stage
   * chat pane) so ctrl+q has a chat pane to close. */
  async function setupApp() {
    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', {
      invalidation_key: 'iv',
      sessions: [
        { agent_id: 'collab-1', role: 'collaborator', status: 'idle', session_name: 'TestCollab' },
      ],
    });
    fake.stubRpc('state.plan_display', { name: 'my-plan', markdown: 'doc body line' });
    const { store, dispose } = createAppStore(fake);
    await store.getState().actions.roster.refresh();
    const inputStores = createInputStores([]);
    const tree = render(<App store={store} inputStores={inputStores} bus={fake} />);
    return { fake, store, dispose, inputStores, ...tree };
  }

  it('closes the highlighted chat-history pane and re-homes focus to chat', async () => {
    const { store, inputStores, stdin, dispose } = await setupApp();
    await tick();

    // The default-favorited collaborator's chat pane is open + mounted; focus it.
    inputStores.focus.getState().focus('stage:chat:collab-1');
    await tick();
    expect(selectEffectiveFocus(inputStores.focus)).toBe('stage:chat:collab-1');

    stdin.write(CTRL_Q);
    await tick();

    // The pane closed: an explicit `false` paneOverride hides even the default-favorited collaborator.
    expect(store.getState().conversations.paneOverrides.get('collab-1')).toBe(false);
    // The pane unmounted → focus re-homes to chat (the derived invariant).
    expect(selectEffectiveFocus(inputStores.focus)).toBe(CHAT_FOCUS);
    dispose();
  });

  it('closes the highlighted doc pane', async () => {
    const { store, inputStores, stdin, dispose } = await setupApp();
    await tick();

    // Open a doc and focus its Stage pane.
    await store.getState().actions.docView.open('plan', 'my-plan');
    inputStores.focus.getState().focus('stage:doc:my-plan');
    await tick();
    expect(store.getState().docView.open).not.toBeNull();
    expect(selectEffectiveFocus(inputStores.focus)).toBe('stage:doc:my-plan');

    stdin.write(CTRL_Q);
    await tick();

    expect(store.getState().docView.open).toBeNull();
    expect(selectEffectiveFocus(inputStores.focus)).toBe(CHAT_FOCUS);
    dispose();
  });

  it('does nothing when the chat input is focused (no Stage pane highlighted)', async () => {
    const { store, inputStores, stdin, dispose } = await setupApp();
    await tick();
    // Focus stays on chat (the default home).
    expect(selectEffectiveFocus(inputStores.focus)).toBe(CHAT_FOCUS);

    stdin.write(CTRL_Q);
    await tick();

    // No pane override written; the collaborator pane remains open.
    expect(store.getState().conversations.paneOverrides.has('collab-1')).toBe(false);
    dispose();
  });
});

describe('theme bridge (Phase 5) — settings.theme → themeStore', () => {
  beforeEach(() => {
    themeStore.getState().setTheme(DEFAULT_THEME_ID);
  });
  afterEach(() => {
    themeStore.getState().setTheme(DEFAULT_THEME_ID);
  });

  it('loads the persisted theme into the global themeStore on mount', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', { invalidation_key: 'iv', sessions: [] });
    // settings.get resolves with the light theme → the bridge applies it after load.
    fake.stubRpc('settings.get', {
      ok: true,
      settings: { theme: 'everforest-light', modifier: 'alt', key_overrides: {}, pane_gap: 0 },
    });
    const { store, dispose } = createAppStore(fake);
    const inputStores = createInputStores([]);
    const { unmount } = render(<App store={store} inputStores={inputStores} bus={fake} />);
    await tick();
    expect(themeStore.getState().id).toBe('everforest-light');
    unmount();
    dispose();
  });

  it('reacts to a live settings.theme change (the optimistic update path)', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', { invalidation_key: 'iv', sessions: [] });
    fake.stubRpc('settings.update', {
      ok: true,
      settings: { theme: 'everforest-light', modifier: 'alt', key_overrides: {}, pane_gap: 0 },
    });
    const { store, dispose } = createAppStore(fake);
    const inputStores = createInputStores([]);
    const { unmount } = render(<App store={store} inputStores={inputStores} bus={fake} />);
    await tick();
    expect(themeStore.getState().id).toBe(DEFAULT_THEME_ID);

    // An optimistic update overlays the slice synchronously; the bridge subscription mirrors it.
    await store.getState().actions.settings.update({ theme: 'everforest-light' });
    await tick();
    expect(themeStore.getState().id).toBe('everforest-light');
    unmount();
    dispose();
  });

  it('falls back to the default theme for an unknown persisted id', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', { invalidation_key: 'iv', sessions: [] });
    fake.stubRpc('settings.get', {
      ok: true,
      settings: { theme: 'solarized-unknown', modifier: 'alt', key_overrides: {}, pane_gap: 0 },
    });
    const { store, dispose } = createAppStore(fake);
    const inputStores = createInputStores([]);
    const { unmount } = render(<App store={store} inputStores={inputStores} bus={fake} />);
    await tick();
    // Unknown id → validated to the default scheme (never an uncolored UI).
    expect(themeStore.getState().id).toBe(DEFAULT_THEME_ID);
    unmount();
    dispose();
  });
});
