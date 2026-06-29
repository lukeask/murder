/**
 * App-shell integration test — the skeleton-level companion to the per-panel test idiom
 * Renders the real {@link App} against a
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
import { createInputStores } from '../src/input/createInputStores.js';
import { stageTranscriptFocusId } from '../src/input/focusIds.js';
import { CHAT_FOCUS, selectEffectiveFocus } from '../src/input/focusStore.js';
import type { PanelId } from '../src/input/panels.js';
import type { SettingsWire } from '../src/store/settings/settingsActions.js';
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

function settingsWire(overrides: Partial<SettingsWire> = {}): SettingsWire {
  return {
    theme: DEFAULT_THEME_ID,
    modifier: 'alt',
    key_overrides: {},
    pane_gap: 0,
    vim_mode: false,
    default_chat_view_mode: 'verbose',
    startup_rogue: null,
    collaborator_harness: null,
    planner_harness: null,
    crow_harnesses: null,
    effective_collaborator_harness: 'claude_code',
    effective_planner_harness: 'claude_code',
    effective_crow_harnesses: ['claude_code'],
    llm: {},
    llm_env: { groq: false, cerebras: false, openrouter: false },
    ...overrides,
  };
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

  it('mounts a favorited crow transcript pane in the center-stage group (not the crows list pane)', async () => {
    // App-path test: renders the full App and proves the favorited crow's transcript pane is
    // mounted in the center-stage group via the pane bridge.
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
    // Crows list pane header is present (confirms the crows region rendered).
    expect(frame).toContain('Crows');
    // The center-stage group shows a pane titled for the collaborator.
    expect(frame).toContain('TestCollab');
    dispose();
  });

  // TUIchat-5: the fullscreen tmux mode (and its `presentationHidesLayout` suppression test) was
  // retired — tmux is now an inline per-pane view in TranscriptPane.tsx, never a layout takeover. View-mode
  // cycling is covered in conversationsActions.test.ts; inline frame rendering has no dedicated test yet.
});

describe('deriveSpawnContext — doc file context gated by the highlighted center-stage pane', () => {
  /** Build a minimal FakeBusClient-backed store. */
  function makeStore() {
    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', { invalidation_key: 'iv', sessions: [] });
    return createAppStore(fake);
  }

  it('returns null when no doc is open (the closed doc-view default)', () => {
    const { store, dispose } = makeStore();
    expect(deriveSpawnContext(store, { kind: 'docPane', name: 'anything' })).toBeNull();
    dispose();
  });

  it('returns the open note when the doc pane is highlighted (reference-by-path)', () => {
    const { store, dispose } = makeStore();
    store.setState((s) => ({
      docView: { ...s.docView, open: { kind: 'note', name: 'my-note' } },
    }));
    expect(deriveSpawnContext(store, { kind: 'docPane', name: 'my-note' })).toEqual({
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
    expect(deriveSpawnContext(store, { kind: 'docPane', name: 'my-report' })).toEqual({
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
    expect(deriveSpawnContext(store, { kind: 'docPane', name: 'my-plan' })).toEqual({
      title: 'my-plan',
      path: '.murder/plans/my-plan.md',
    });
    dispose();
  });

  it('returns null when a transcript pane is highlighted, even though a doc is open elsewhere', () => {
    // A highlighted transcript pane gets NO file prompt, even if a doc is open elsewhere in the
    // center-stage group — the file context follows the highlight, not the open slice.
    const { store, dispose } = makeStore();
    store.setState((s) => ({
      docView: { ...s.docView, open: { kind: 'plan', name: 'my-plan' } },
    }));
    expect(deriveSpawnContext(store, { kind: 'transcriptPane', agentId: 'crow-1' })).toBeNull();
    dispose();
  });

  it('returns null when the chat input is focused (no center-stage pane highlighted)', () => {
    const { store, dispose } = makeStore();
    store.setState((s) => ({
      docView: { ...s.docView, open: { kind: 'plan', name: 'my-plan' } },
    }));
    expect(deriveSpawnContext(store, { kind: 'composer' })).toBeNull();
    dispose();
  });
});

describe('ctrl+q — close the highlighted center-stage pane', () => {
  // ctrl+q rides the clean legacy byte 0x11, which Ink reports as `{ ctrl: true, input: 'q' }`.
  const CTRL_Q = '\x11';

  /** A full App against fakes, with a roster carrying one default-favorited collaborator (→ one center-stage
   * transcript pane) so ctrl+q has a transcript pane to close. */
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

  it('closes the highlighted transcript pane and re-homes focus to chat', async () => {
    const { store, inputStores, stdin, dispose } = await setupApp();
    await tick();

    // The default-favorited collaborator's transcript pane is open + mounted; focus it.
    inputStores.focus.getState().focus(stageTranscriptFocusId('collab-1'));
    await tick();
    expect(selectEffectiveFocus(inputStores.focus)).toBe(stageTranscriptFocusId('collab-1'));

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

    // Open a doc and focus its center-stage pane.
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

  it('does nothing when the chat input is focused (no center-stage pane highlighted)', async () => {
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
      settings: settingsWire({ theme: 'everforest-light' }),
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
      settings: settingsWire({ theme: 'everforest-light' }),
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
      settings: settingsWire({ theme: 'solarized-unknown' }),
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
