/**
 * Settings actions — the *only* code that calls the bus for TUI preferences (rule 3).
 *
 * Two RPCs, modeled exactly on the favorites prefs pair:
 *  - `settings.get {}` → `{ ok, settings: { theme, modifier, key_overrides } }` — load the persisted
 *     preferences.
 *  - `settings.update { settings: {partial} }` → `{ ok, settings: {full merged} }` — overlay a
 *     partial patch onto the persisted config and persist; the reply is the full merged record.
 * Both are declared via a `declare module` augmentation of the shared {@link RpcMethods} registry so
 * the C1/C2 bus files (`BusClient.ts`/`UdsBusClient.ts`) stay byte-identical — the seam (rule 4). The
 * keys (`settings.get`/`settings.update`) are distinct from every other slice's keys.
 *
 * ## Wire vs. slice naming
 *
 * The wire uses snake_case `key_overrides` (it mirrors the Python `TuiUserConfig`); the slice uses
 * camelCase `keyOverrides`. This action is the single translation point between the two.
 *
 * ## Optimistic local-first writes
 *
 * `update(partial)` overlays the patch onto the local slice immediately (a settings change must feel
 * instant — the dispatcher/keymaps/footer react off the bridged stores at once) and THEN fires
 * `settings.update` with the same partial. The local slice is the source of truth for the session;
 * the RPC is persistence. A save rejection sets `error` but does NOT roll back — the user's intent
 * stands; a reconnect/restart re-loads from the persisted truth. (No `state.snapshot` event for
 * settings — cross-client live-sync is a known out-of-scope limitation.)
 */

import type { StoreApi } from 'zustand';
import type { BusClient } from '../../bus/BusClient.js';
import type { AppStore } from '../store.js';
import { toastStore } from '../toast/toastStore.js';
import type { SettingsModifier, SettingsState } from './settingsSlice.js';

/** The on-the-wire settings record (snake_case, mirrors the Python `TuiUserConfig`). The frontend
 * binding registry is the authority on `ActionId`s, so `key_overrides` is opaque here. */
export interface SettingsWire {
  readonly theme: string;
  readonly modifier: SettingsModifier;
  readonly key_overrides: Readonly<Record<string, string>>;
  /** Spaces of inter-pane-border gap (0–4). Mirrors the Python `TuiUserConfig.pane_gap`. */
  readonly pane_gap: number;
}

/** A partial settings patch for `update` — any subset of the wire fields. */
export type SettingsPatch = Partial<SettingsWire>;

/**
 * Phase 3's settings RPC declarations, augmenting the shared {@link RpcMethods} registry without
 * editing the frozen C1/C2 bus files (rule 4 — the seam). Keys distinct from every other slice's.
 * Shapes mirror the Python `settings.{get,update}` handlers in `murder/app/service/host.py`.
 */
declare module '../../bus/BusClient.js' {
  interface RpcMethods {
    /** Load the persisted TUI preferences. Empty params; reply carries the full settings record. */
    'settings.get': {
      params: Record<string, never>;
      result: { ok: boolean; settings: SettingsWire };
    };
    /** Persist a partial preferences patch; reply echoes the full merged record. */
    'settings.update': {
      params: { settings: SettingsPatch };
      result: { ok: boolean; settings: SettingsWire };
    };
  }
}

/** The settings actions, bound to one {@link BusClient} + store handle. */
export interface SettingsActions {
  /**
   * Load the persisted settings via `settings.get` (once, at startup). Ref-swaps the slice to
   * `loading`, then `ready` with the loaded record (or `error` on rejection — never thrown past the
   * action, so the startup boot stays fire-and-forget; settings stay at their defaults on failure).
   */
  load(): Promise<void>;
  /**
   * Overlay a partial settings patch locally (optimistic), then persist via `settings.update`. The
   * patch is in wire shape (`key_overrides`); the slice mirrors it onto `keyOverrides`. Local-first:
   * the slice changes immediately; the RPC is fire-and-forget persistence (a rejection lands in
   * `error` + a toast, no rollback).
   */
  update(partial: SettingsPatch): Promise<void>;
}

/** Project a `settings.get`/`settings.update` reply's wire record onto the slice's camelCase shape,
 * defensively (the wire may omit a field — fall back to the current state's value). */
function applyWire(prev: SettingsState, wire: SettingsWire | undefined): SettingsState {
  if (wire === undefined) {
    return { ...prev, status: 'ready', error: null };
  }
  return {
    theme: wire.theme ?? prev.theme,
    modifier: wire.modifier ?? prev.modifier,
    keyOverrides: wire.key_overrides ?? prev.keyOverrides,
    paneGap: wire.pane_gap ?? prev.paneGap,
    status: 'ready',
    error: null,
  };
}

export function createSettingsActions(bus: BusClient, store: StoreApi<AppStore>): SettingsActions {
  return {
    async load(): Promise<void> {
      store.setState((state) => ({ settings: { ...state.settings, status: 'loading' } }));
      try {
        const reply = await bus.rpc('settings.get', {});
        store.setState((state) => ({ settings: applyWire(state.settings, reply.settings) }));
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        store.setState((state) => ({
          settings: { ...state.settings, status: 'error', error: message },
        }));
      }
    },

    async update(partial: SettingsPatch): Promise<void> {
      // Optimistic local overlay — translate the wire patch onto the camelCase slice immediately.
      store.setState((state) => ({
        settings: {
          ...state.settings,
          ...(partial.theme !== undefined ? { theme: partial.theme } : {}),
          ...(partial.modifier !== undefined ? { modifier: partial.modifier } : {}),
          ...(partial.key_overrides !== undefined ? { keyOverrides: partial.key_overrides } : {}),
          ...(partial.pane_gap !== undefined ? { paneGap: partial.pane_gap } : {}),
          status: 'ready',
          error: null,
        },
      }));
      try {
        await bus.rpc('settings.update', { settings: partial });
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        // Fire-and-forget persist rejection (the change already applied locally; no open form to host
        // an inline error) — surface via the global toast, and record it on the slice `error` (the
        // "intent stands; reconnect re-loads" model the favorites pair documents).
        store.setState((state) => ({ settings: { ...state.settings, error: message } }));
        toastStore.getState().push(message, { severity: 'error', ttlMs: 6000 });
      }
    },
  };
}
