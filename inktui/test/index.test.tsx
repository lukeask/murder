/**
 * Entrypoint runner test (F7). The live/smoke render paths use Ink's real `render`, which patches
 * `console` and cannot run under Vitest's environment (`ink-testing-library` is what the shell tests
 * use instead — see App.test.tsx); those paths are proven by an actual `node dist/index.js --smoke`
 * run at build time. What this file pins is the pure, load-bearing wiring F7 introduces:
 *
 *   - the socket path is taken from `MURDER_BUS_SOCKET` **verbatim** — the no-rehash invariant: the
 *     TS side never derives the per-project socket path, it only connects to what the launcher hands
 *     it (Open decision #2);
 *   - a missing/empty `MURDER_BUS_SOCKET` is a clear, hard failure rather than a silent bad connect.
 */

import { describe, expect, it } from 'vitest';
import { FakeBusClient } from '../src/bus/FakeBusClient.js';
import { primeSlices, resolveSocketPath } from '../src/index.js';
import type { SettingsWire } from '../src/store/settings/settingsActions.js';
import { createAppStore } from '../src/store/store.js';

describe('resolveSocketPath', () => {
  it('returns MURDER_BUS_SOCKET verbatim (no rehashing the per-project path)', () => {
    const path = '/run/user/1000/murder/repo-abc123def456/bus.sock';
    expect(resolveSocketPath({ MURDER_BUS_SOCKET: path })).toBe(path);
  });

  it('throws a clear error naming the env var when it is unset', () => {
    expect(() => resolveSocketPath({})).toThrow(/MURDER_BUS_SOCKET is not set/);
  });

  it('throws when MURDER_BUS_SOCKET is empty or whitespace', () => {
    expect(() => resolveSocketPath({ MURDER_BUS_SOCKET: '   ' })).toThrow(/MURDER_BUS_SOCKET/);
  });
});

/** A canned `settings.get` reply with a non-default modifier, so a successful prime is visible as a
 * change away from `initialSettingsState` (which uses `alt`). */
function settingsWire(overrides: Partial<SettingsWire> = {}): SettingsWire {
  return {
    theme: 'everforest-dark',
    modifier: 'ctrl',
    key_overrides: {},
    pane_gap: 0,
    vim_mode: false,
    default_chat_view_mode: 'verbose',
    startup_rogue: null,
    collaborator_harness: null,
    planner_harness: null,
    crow_harnesses: ['cursor', 'claude_code'],
    effective_collaborator_harness: 'claude_code',
    effective_planner_harness: 'claude_code',
    effective_crow_harnesses: ['cursor', 'claude_code'],
    llm: {},
    llm_env: { groq: false, cerebras: false, openrouter: false },
    ...overrides,
  };
}

/**
 * Regression guard for the settings-wipe bug: `primeSlices` runs on every (re)connect, but settings
 * was the lone persisted slice missing from it — it loaded once from a mount-effect with no retry, so
 * a `settings.get` that raced the daemon socket after `murder up` stranded the slice at its defaults
 * (modifier `alt`, crow-harness fallback) for the whole session even though config.yaml was intact.
 * Priming settings here is what lets the slice self-heal on reconnect like every other slice.
 */
describe('primeSlices', () => {
  it('re-fetches settings on every (re)connect (settings-wipe regression)', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('settings.get', { ok: true, settings: settingsWire() });
    const { store, dispose } = createAppStore(fake);

    expect(store.getState().settings.modifier).toBe('alt'); // pre-prime default

    primeSlices(store);
    await new Promise((r) => setTimeout(r, 0)); // let the fire-and-forget RPCs settle

    expect(fake.rpcCalls.some((c) => c.method === 'settings.get')).toBe(true);
    const settings = store.getState().settings;
    expect(settings.status).toBe('ready');
    expect(settings.modifier).toBe('ctrl'); // loaded from the reply, not stranded at the default
    dispose();
  });

  it('also primes favorites on (re)connect (the sibling self-heal it mirrors)', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('settings.get', { ok: true, settings: settingsWire() });
    fake.stubRpc('tui.load_favorites', { ok: true, favorites: [] });
    const { store, dispose } = createAppStore(fake);

    primeSlices(store);
    await new Promise((r) => setTimeout(r, 0));

    expect(fake.rpcCalls.some((c) => c.method === 'tui.load_favorites')).toBe(true);
    dispose();
  });
});
