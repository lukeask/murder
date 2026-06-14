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

/** The four user-configurable LLM provider ids (mirrors the Python `UserLlmConfig.providers` keys).
 * `local` is the OpenAI-compatible local endpoint (no api-key env flag). */
export type LlmProviderId = 'groq' | 'cerebras' | 'openrouter' | 'local';

/** The provider ids that carry an env-key flag (`local` has none — env beats config.yaml only for
 * these three; see the Python `_settings_payload`'s `llm_env`). */
export type LlmEnvProviderId = 'groq' | 'cerebras' | 'openrouter';

/** The tier `provider` enum (a superset of {@link LlmProviderId} — tiers may point at `anthropic` /
 * `openai` too; mirrors the Python `UserLlmTier.provider`). */
export type LlmTierProvider = 'openrouter' | 'anthropic' | 'openai' | 'local' | 'cerebras' | 'groq';

/** One provider's stored credentials. `api_key` is masked `"***"` on `get` when a key is stored,
 * `null`/absent when unset; on `update` `"***"` means "leave unchanged" and `""` clears. `base_url`
 * is meaningful for `local` (the OpenAI-compatible endpoint). */
export interface LlmProviderWire {
  readonly api_key?: string | null;
  readonly base_url?: string | null;
}

/** A named tier: a `(provider, model)` pair a role can bind to. Mirrors the Python `UserLlmTier`. */
export interface LlmTierWire {
  readonly provider: LlmTierProvider;
  readonly model: string;
  readonly auto_free?: boolean;
}

/** The LLM block — `{}` when nothing is set. `tiers`/`roles` are open string-keyed maps; the built-in
 * `cheap`/`smart` tiers exist server-side even when `tiers` is empty (the UI lists them regardless). */
export interface LlmWire {
  readonly providers?: Readonly<Partial<Record<LlmProviderId, LlmProviderWire>>>;
  readonly tiers?: Readonly<Record<string, LlmTierWire>>;
  readonly roles?: Readonly<Record<string, string>>;
}

/** Whether each env-flagged provider's api key is present in the daemon's environment (env/.env always
 * beats config.yaml). When `true` the config value is ignored — display only. */
export type LlmEnvWire = Readonly<Record<LlmEnvProviderId, boolean>>;

/** The on-the-wire settings record (snake_case, mirrors the Python `_settings_payload`). The frontend
 * binding registry is the authority on `ActionId`s, so `key_overrides` is opaque here. */
export interface SettingsWire {
  // --- tui prefs (unchanged) ---
  readonly theme: string;
  readonly modifier: SettingsModifier;
  readonly key_overrides: Readonly<Record<string, string>>;
  /** Spaces of inter-pane-border gap (0–4). Mirrors the Python `TuiUserConfig.pane_gap`. */
  readonly pane_gap: number;
  /** Whether vim-style editing is enabled in the chat input. Mirrors `TuiUserConfig.vim_mode`. */
  readonly vim_mode: boolean;
  // --- harness overrides + daemon's live effective values ---
  /** The user's collaborator-harness override, or `null` when none is set. */
  readonly collaborator_harness: string | null;
  /** The user's crow-harness pool override, or `null` when none is set. */
  readonly crow_harnesses: readonly string[] | null;
  /** The daemon's live merged collaborator harness (override → role default). */
  readonly effective_collaborator_harness: string;
  /** The daemon's live merged crow-harness pool. */
  readonly effective_crow_harnesses: readonly string[];
  // --- llm provider/tier/role config (api keys masked) ---
  readonly llm: LlmWire;
  readonly llm_env: LlmEnvWire;
}

/** A partial LLM patch for `update` — deep-merged server-side. Sending an `api_key` of `"***"` leaves
 * it unchanged; `""` clears it. The deep-merge cannot delete keys (so omit, never null, a tier/role). */
export interface LlmPatch {
  readonly providers?: Partial<Record<LlmProviderId, LlmProviderWire>>;
  readonly tiers?: Record<string, LlmTierWire>;
  readonly roles?: Record<string, string>;
}

/** A partial settings patch for `update`. Any subset of the tui keys, the harness overrides
 * (`collaborator_harness: string|null`, `crow_harnesses: non-empty string[]|null`), and `llm`. */
export interface SettingsPatch {
  theme?: string;
  modifier?: SettingsModifier;
  key_overrides?: Readonly<Record<string, string>>;
  pane_gap?: number;
  vim_mode?: boolean;
  collaborator_harness?: string | null;
  crow_harnesses?: readonly string[] | null;
  llm?: LlmPatch;
}

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
    vimMode: wire.vim_mode ?? prev.vimMode,
    // Harness overrides are nullable on the wire (null = "no override"), so a `??` would wrongly keep
    // the prior value when the server clears one. Honour `null` explicitly via the key-presence check.
    collaboratorHarness:
      'collaborator_harness' in wire ? wire.collaborator_harness : prev.collaboratorHarness,
    crowHarnesses: 'crow_harnesses' in wire ? wire.crow_harnesses : prev.crowHarnesses,
    effectiveCollaboratorHarness:
      wire.effective_collaborator_harness ?? prev.effectiveCollaboratorHarness,
    effectiveCrowHarnesses: wire.effective_crow_harnesses ?? prev.effectiveCrowHarnesses,
    llm: wire.llm ?? prev.llm,
    llmEnv: wire.llm_env ?? prev.llmEnv,
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
      // The tui prefs + harness overrides apply cleanly local-first (the dispatcher/layout/spawn flow
      // react at once). The `llm` block is NOT overlaid optimistically: its api_key masking and the
      // server-computed `effective_*`/built-in tiers make a faithful local merge impractical, so we
      // rely on applying the reply's full payload below to refresh the llm view.
      store.setState((state) => ({
        settings: {
          ...state.settings,
          ...(partial.theme !== undefined ? { theme: partial.theme } : {}),
          ...(partial.modifier !== undefined ? { modifier: partial.modifier } : {}),
          ...(partial.key_overrides !== undefined ? { keyOverrides: partial.key_overrides } : {}),
          ...(partial.pane_gap !== undefined ? { paneGap: partial.pane_gap } : {}),
          ...(partial.vim_mode !== undefined ? { vimMode: partial.vim_mode } : {}),
          ...('collaborator_harness' in partial
            ? { collaboratorHarness: partial.collaborator_harness ?? null }
            : {}),
          ...('crow_harnesses' in partial ? { crowHarnesses: partial.crow_harnesses ?? null } : {}),
          status: 'ready',
          error: null,
        },
      }));
      try {
        const reply = await bus.rpc('settings.update', { settings: partial });
        // Apply the full merged reply — refreshes llm (masked keys), the `effective_*` harness values,
        // and reconciles any server-side normalisation of the optimistic overlay.
        store.setState((state) => ({ settings: applyWire(state.settings, reply.settings) }));
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
