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
import type { BarWidgetsConfig } from '../../selectors/barWidgetRegistry.js';
import type { AppStore } from '../store.js';
import { toastStore } from '../toast/toastStore.js';
import type {
  DefaultChatViewMode,
  DocumentDisplayMode,
  SettingsModifier,
  SettingsState,
} from './settingsSlice.js';

/** The four user-configurable LLM provider ids (mirrors the Python `UserLlmConfig.providers` keys).
 * `local` is the OpenAI-compatible local endpoint (no api-key env flag). */
export type LlmProviderId = 'groq' | 'cerebras' | 'openrouter' | 'local';

/** The provider ids that carry an env-key flag (`local` has none — env beats config.yaml only for
 * these three; see the Python `_settings_payload`'s `llm_env`). */
export type LlmEnvProviderId = 'groq' | 'cerebras' | 'openrouter';

/** The tier `provider` enum (a superset of {@link LlmProviderId} — tiers may point at `anthropic` /
 * `openai` too; mirrors the Python `UserLlmTier.provider`). */
export type LlmTierProvider = 'groq' | 'cerebras' | 'local' | 'anthropic' | 'openai' | 'openrouter';

/** One provider's stored credentials. `api_key` is masked `"***"` on `get` when a key is stored,
 * `null`/absent when unset; on `update` `"***"` means "leave unchanged" and `""` clears. `base_url`
 * is meaningful for `local` (the OpenAI-compatible endpoint). */
export interface LlmProviderWire {
  readonly type?: string | null;
  readonly name?: string | null;
  readonly enabled?: boolean;
  readonly endpoint?: string | null;
  readonly auth?: { readonly api_key?: string | null };
  readonly api_key?: string | null;
  readonly base_url?: string | null;
  readonly models?: {
    readonly source?: 'recommended' | 'discovered' | 'custom';
    readonly include?: readonly string[];
    readonly exclude?: readonly string[];
    readonly overrides?: Readonly<Record<string, LlmModelOverrideWire>>;
  };
}

export interface LlmModelOverrideWire {
  readonly enabled?: boolean;
  readonly locality?: 'local' | 'remote' | 'unknown';
  readonly cost_class?: 'free' | 'paid' | 'unknown';
  readonly tags?: readonly string[];
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
  readonly disabled?: boolean;
  readonly active_policy?: string | null;
  readonly providers?: Readonly<Record<string, LlmProviderWire>>;
  readonly tiers?: Readonly<Record<string, LlmTierWire>>;
  readonly roles?: Readonly<Record<string, string>>;
  readonly policies?: Readonly<Record<string, LlmPolicyWire>>;
  readonly feature_policies?: Readonly<Record<string, string>>;
}

export interface LlmPolicyWire {
  readonly builtin: boolean;
  readonly name?: string | null;
  readonly groups?: readonly { readonly selectors: readonly unknown[] }[];
}

/** Whether each env-flagged provider's api key is present in the daemon's environment (env/.env always
 * beats config.yaml). When `true` the config value is ignored — display only. */
export type LlmEnvWire = Readonly<Record<LlmEnvProviderId, boolean>>;

/** The auto-spawned-on-boot rogue ("Startup Rogue"). Mirrors the Python `StartupRogueConfig`:
 * `model` empty = the harness adapter's default; `effort` `null` = no reasoning-effort override.
 * The whole record is `null` on the wire when no startup rogue is configured. */
export interface StartupRogueWire {
  readonly harness: string;
  readonly model: string;
  readonly effort: string | null;
}

export interface StartupRogueModelWire {
  readonly id: string;
  readonly label: string;
}

/** The on-the-wire settings record (snake_case, mirrors the Python `_settings_payload`). The frontend
 * binding registry is the authority on `ActionId`s, so `key_overrides` is opaque here. */
export interface SettingsWire {
  // --- tui prefs (unchanged) ---
  readonly theme: string;
  readonly modifier: SettingsModifier;
  readonly key_overrides: Readonly<Record<string, string>>;
  /** Spaces of inter-pane-border gap (0–4). Mirrors the Python `TuiUserConfig.pane_gap`. */
  readonly pane_gap: number;
  /** Number of virtual workspaces (1–9). Mirrors the Python `TuiUserConfig.workspace_count`. */
  readonly workspace_count: number;
  /** Whether vim-style editing is enabled in the chat input. Mirrors `TuiUserConfig.vim_mode`. */
  readonly vim_mode: boolean;
  /** Per-widget bar configuration. Mirrors `TuiUserConfig.bar_widgets`. */
  readonly bar_widgets: BarWidgetsConfig;
  /** Default chat view mode for panes with no override. Mirrors `TuiUserConfig.default_chat_view_mode`. */
  readonly default_chat_view_mode: DefaultChatViewMode;
  /** Document interpretation mode. Optional for compatibility with an older daemon snapshot. */
  readonly document_display_mode?: DocumentDisplayMode;
  /** The user's Startup Rogue (auto-spawned on boot), or `null` when none is set. */
  readonly startup_rogue: StartupRogueWire | null;
  /** Available Startup Rogue model choices by harness. */
  readonly startup_rogue_models?: Readonly<Record<string, readonly StartupRogueModelWire[]>>;
  /** Available Startup Rogue effort choices by harness. */
  readonly startup_rogue_efforts?: Readonly<Record<string, readonly string[]>>;
  // --- harness overrides + daemon's live effective values ---
  /** The user's collaborator-harness override, or `null` when none is set. */
  readonly collaborator_harness: string | null;
  /** The user's planning-agent harness override, or `null` when none is set. */
  readonly planner_harness: string | null;
  /** The user's crow-harness pool override, or `null` when none is set. */
  readonly crow_harnesses: readonly string[] | null;
  /** The daemon's live merged collaborator harness (override → role default). */
  readonly effective_collaborator_harness: string;
  /** The daemon's live merged planning-agent harness. */
  readonly effective_planner_harness: string;
  /** The daemon's live merged crow-harness pool. */
  readonly effective_crow_harnesses: readonly string[];
  // --- llm provider/tier/role config (api keys masked) ---
  readonly llm: LlmWire;
  readonly llm_env: LlmEnvWire;
}

/** A partial LLM patch for `update` — deep-merged server-side. Sending an `api_key` of `"***"` leaves
 * it unchanged; `""` clears it. The deep-merge cannot delete keys (so omit, never null, a tier/role). */
export interface LlmPatch {
  readonly providers?: Record<string, LlmProviderWire>;
  readonly feature_policies?: Record<string, string>;
  readonly tiers?: Record<string, LlmTierWire>;
  readonly roles?: Record<string, string>;
}

/** A partial settings patch for `update`. Any subset of the tui keys, the harness overrides
 * (`collaborator_harness` / `planner_harness` / `crow_harnesses`), and `llm`. */
export interface SettingsPatch {
  theme?: string;
  modifier?: SettingsModifier;
  key_overrides?: Readonly<Record<string, string>>;
  pane_gap?: number;
  workspace_count?: number;
  vim_mode?: boolean;
  bar_widgets?: BarWidgetsConfig;
  default_chat_view_mode?: DefaultChatViewMode;
  document_display_mode?: DocumentDisplayMode;
  /** Set/replace the Startup Rogue, or `null` to clear it. */
  startup_rogue?: StartupRogueWire | null;
  collaborator_harness?: string | null;
  planner_harness?: string | null;
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
    'llm.settings.set_disabled': {
      params: { disabled: boolean };
      result: { ok: boolean; llm: LlmWire; settings: SettingsWire };
    };
    'llm.provider.create': {
      params: { provider: Record<string, unknown> };
      result: { ok: boolean; provider_id: string; llm: LlmWire; settings: SettingsWire };
    };
    'llm.provider.update': {
      params: { provider_id: string; patch: Record<string, unknown> };
      result: { ok: boolean; llm: LlmWire; settings: SettingsWire };
    };
    'llm.provider.delete': {
      params: { provider_id: string; confirm: true };
      result: { ok: boolean; llm: LlmWire; settings: SettingsWire };
    };
    'llm.provider.discover_models': {
      params: { provider_id: string };
      result: { ok: boolean; models: readonly { id: string; label: string }[] };
    };
    'llm.provider.models.update': {
      params: { provider_id: string; patch: Record<string, unknown> };
      result: { ok: boolean; llm: LlmWire; settings: SettingsWire };
    };
    'llm.policy.create': {
      params: { name: string; policy?: Record<string, unknown> };
      result: { ok: boolean; policy_id: string; llm: LlmWire; settings: SettingsWire };
    };
    'llm.policy.update': {
      params: { policy_id: string; patch: Record<string, unknown> };
      result: { ok: boolean; llm: LlmWire; settings: SettingsWire };
    };
    'llm.policy.delete': {
      params: { policy_id: string; confirm: true };
      result: { ok: boolean; llm: LlmWire; settings: SettingsWire };
    };
    'llm.policy.activate': {
      params: { policy_id: string };
      result: { ok: boolean; llm: LlmWire; settings: SettingsWire };
    };
    'llm.policy.clone': {
      params: { policy_id: string; name: string };
      result: { ok: boolean; policy_id: string; llm: LlmWire; settings: SettingsWire };
    };
    'llm.feature_policy.set': {
      params: { feature_type: string; policy_id: string | null };
      result: { ok: boolean; llm: LlmWire; settings: SettingsWire };
    };
    'llm.preview_resolution': {
      params: {
        feature_type: string;
        required_capabilities?: readonly string[];
        required_execution_mode?: string | null;
        min_context_tokens?: number | null;
      };
      result: {
        ok: boolean;
        status: string;
        policy_id: string | null;
        candidates: readonly {
          provider_id: string;
          provider_type: string;
          model_id: string;
          locality: string;
          cost_class: string;
        }[];
      };
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
  readonly llm: LlmActions;
}

/** Direct-LLM configuration operations. The service returns its masked,
 * authoritative config after each mutation so the TUI never merges secrets. */
export interface LlmActions {
  setDisabled(disabled: boolean): Promise<void>;
  createProvider(provider: Record<string, unknown>): Promise<string | null>;
  updateProvider(providerId: string, patch: Record<string, unknown>): Promise<void>;
  updateProviderModels(providerId: string, patch: Record<string, unknown>): Promise<void>;
  deleteProvider(providerId: string): Promise<void>;
  discoverModels(providerId: string): Promise<readonly { id: string; label: string }[]>;
  createPolicy(name: string, policy?: Record<string, unknown>): Promise<string | null>;
  updatePolicy(policyId: string, patch: Record<string, unknown>): Promise<void>;
  deletePolicy(policyId: string): Promise<void>;
  activatePolicy(policyId: string): Promise<void>;
  clonePolicy(policyId: string, name: string): Promise<string | null>;
  setFeaturePolicy(featureType: string, policyId: string | null): Promise<void>;
  previewResolution(featureType: string): Promise<readonly { provider_id: string; model_id: string }[]>;
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
    workspaceCount: wire.workspace_count ?? prev.workspaceCount,
    vimMode: wire.vim_mode ?? prev.vimMode,
    barWidgets: wire.bar_widgets ?? prev.barWidgets,
    defaultChatViewMode: wire.default_chat_view_mode ?? prev.defaultChatViewMode,
    documentDisplayMode: wire.document_display_mode ?? prev.documentDisplayMode,
    // Nullable on the wire (null = "no startup rogue") — honour `null` via key-presence, not `??`.
    startupRogue: 'startup_rogue' in wire ? wire.startup_rogue : prev.startupRogue,
    startupRogueModels: wire.startup_rogue_models ?? prev.startupRogueModels,
    startupRogueEfforts: wire.startup_rogue_efforts ?? prev.startupRogueEfforts,
    // Harness overrides are nullable on the wire (null = "no override"), so a `??` would wrongly keep
    // the prior value when the server clears one. Honour `null` explicitly via the key-presence check.
    collaboratorHarness:
      'collaborator_harness' in wire ? wire.collaborator_harness : prev.collaboratorHarness,
    plannerHarness: 'planner_harness' in wire ? wire.planner_harness : prev.plannerHarness,
    crowHarnesses: 'crow_harnesses' in wire ? wire.crow_harnesses : prev.crowHarnesses,
    effectiveCollaboratorHarness:
      wire.effective_collaborator_harness ?? prev.effectiveCollaboratorHarness,
    effectivePlannerHarness: wire.effective_planner_harness ?? prev.effectivePlannerHarness,
    effectiveCrowHarnesses: wire.effective_crow_harnesses ?? prev.effectiveCrowHarnesses,
    llm: wire.llm ?? prev.llm,
    llmEnv: wire.llm_env ?? prev.llmEnv,
    status: 'ready',
    error: null,
  };
}

export function createSettingsActions(bus: BusClient, store: StoreApi<AppStore>): SettingsActions {
  const applyLlmReply = (reply: { settings: SettingsWire }): void => {
    store.setState((state) => ({ settings: applyWire(state.settings, reply.settings) }));
  };

  const llmFailure = (error: unknown): void => {
    const message = error instanceof Error ? error.message : String(error);
    store.setState((state) => ({ settings: { ...state.settings, error: message } }));
    toastStore.getState().push(message, { severity: 'error', ttlMs: 12000 });
  };

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
          ...(partial.workspace_count !== undefined
            ? { workspaceCount: partial.workspace_count }
            : {}),
          ...(partial.vim_mode !== undefined ? { vimMode: partial.vim_mode } : {}),
          ...(partial.bar_widgets !== undefined ? { barWidgets: partial.bar_widgets } : {}),
          ...(partial.default_chat_view_mode !== undefined
            ? { defaultChatViewMode: partial.default_chat_view_mode }
            : {}),
          ...(partial.document_display_mode !== undefined
            ? { documentDisplayMode: partial.document_display_mode }
            : {}),
          ...('startup_rogue' in partial ? { startupRogue: partial.startup_rogue ?? null } : {}),
          ...('collaborator_harness' in partial
            ? { collaboratorHarness: partial.collaborator_harness ?? null }
            : {}),
          ...('planner_harness' in partial
            ? { plannerHarness: partial.planner_harness ?? null }
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
        toastStore.getState().push(message, { severity: 'error', ttlMs: 12000 });
      }
    },

    llm: {
      async setDisabled(disabled): Promise<void> {
        try {
          applyLlmReply(await bus.rpc('llm.settings.set_disabled', { disabled }));
        } catch (error: unknown) {
          llmFailure(error);
        }
      },
      async createProvider(provider): Promise<string | null> {
        try {
          const reply = await bus.rpc('llm.provider.create', { provider });
          applyLlmReply(reply);
          return reply.provider_id;
        } catch (error: unknown) {
          llmFailure(error);
          return null;
        }
      },
      async updateProvider(provider_id, patch): Promise<void> {
        try {
          applyLlmReply(await bus.rpc('llm.provider.update', { provider_id, patch }));
        } catch (error: unknown) {
          llmFailure(error);
        }
      },
      async updateProviderModels(provider_id, patch): Promise<void> {
        try {
          applyLlmReply(await bus.rpc('llm.provider.models.update', { provider_id, patch }));
        } catch (error: unknown) {
          llmFailure(error);
        }
      },
      async deleteProvider(provider_id): Promise<void> {
        try {
          applyLlmReply(await bus.rpc('llm.provider.delete', { provider_id, confirm: true }));
        } catch (error: unknown) {
          llmFailure(error);
        }
      },
      async discoverModels(provider_id): Promise<readonly { id: string; label: string }[]> {
        try {
          return (await bus.rpc('llm.provider.discover_models', { provider_id })).models;
        } catch (error: unknown) {
          llmFailure(error);
          return [];
        }
      },
      async createPolicy(name, policy): Promise<string | null> {
        try {
          const reply = await bus.rpc('llm.policy.create', { name, ...(policy ? { policy } : {}) });
          applyLlmReply(reply);
          return reply.policy_id;
        } catch (error: unknown) {
          llmFailure(error);
          return null;
        }
      },
      async updatePolicy(policy_id, patch): Promise<void> {
        try {
          applyLlmReply(await bus.rpc('llm.policy.update', { policy_id, patch }));
        } catch (error: unknown) {
          llmFailure(error);
        }
      },
      async deletePolicy(policy_id): Promise<void> {
        try {
          applyLlmReply(await bus.rpc('llm.policy.delete', { policy_id, confirm: true }));
        } catch (error: unknown) {
          llmFailure(error);
        }
      },
      async activatePolicy(policy_id): Promise<void> {
        try {
          applyLlmReply(await bus.rpc('llm.policy.activate', { policy_id }));
        } catch (error: unknown) {
          llmFailure(error);
        }
      },
      async clonePolicy(policy_id, name): Promise<string | null> {
        try {
          const reply = await bus.rpc('llm.policy.clone', { policy_id, name });
          applyLlmReply(reply);
          return reply.policy_id;
        } catch (error: unknown) {
          llmFailure(error);
          return null;
        }
      },
      async setFeaturePolicy(feature_type, policy_id): Promise<void> {
        try {
          applyLlmReply(await bus.rpc('llm.feature_policy.set', { feature_type, policy_id }));
        } catch (error: unknown) {
          llmFailure(error);
        }
      },
      async previewResolution(feature_type): Promise<readonly { provider_id: string; model_id: string }[]> {
        try {
          const reply = await bus.rpc('llm.preview_resolution', { feature_type });
          return reply.candidates.map(({ provider_id, model_id }) => ({ provider_id, model_id }));
        } catch (error: unknown) {
          llmFailure(error);
          return [];
        }
      },
    },
  };
}
