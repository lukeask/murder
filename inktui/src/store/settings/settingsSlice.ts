/**
 * Settings slice — the user's persisted TUI preferences (command modifier, theme, key rebinds),
 * the backbone of the settings menu (Phase 5) and the source the input/theme stores bridge from.
 *
 * ## Why hand-written, not a `listSlice.ts` factory shell
 *
 * Like {@link ../favorites/favoritesSlice.js favorites}, this is none of the list-slice factory's
 * `{ rows, status, error }` shape re-pulled on a `state.snapshot`. Settings are a small fixed record
 * loaded once via `settings.get` and persisted via `settings.update` (never snapshot-invalidated). So
 * it is a hand-written slice with its own shape — the documented precedent for a non-factory,
 * non-snapshot slice (see the favorites module doc).
 *
 * ## Shape mirrors the Python `TuiUserConfig`
 *
 * `modifier` / `theme` / `keyOverrides` are exactly the `settings.{get,update}` wire fields
 * (snake_case `key_overrides` on the wire → camelCase `keyOverrides` here). The frontend's binding
 * registry (`src/input/bindings.ts`) is the authority on `ActionId`s; the slice stores `keyOverrides`
 * opaquely as a `Record<string,string>` (the server does too) — a bridge narrows it onto the
 * bindings store, which knows the `ActionId` union.
 *
 * Ref-swap granularity: every mutation replaces the whole `settings` slice object, so
 * `useAppStore(s => s.settings, shallow)` subscribers re-render only when a preference actually
 * changes — the same granularity contract every slice honours.
 */

import type { StateCreator } from 'zustand';
import type { AppStore } from '../store.js';
import type {
  LlmEnvWire,
  LlmWire,
  StartupRogueModelWire,
  StartupRogueWire,
} from './settingsActions.js';

/** The command modifier the user has chosen. Mirrors `bindings.ts`'s `Modifier` (kept structural
 * here so the slice does not depend on the input layer — the bridge couples them). */
export type SettingsModifier = 'alt' | 'ctrl' | 'both';

/** The settable default chat view mode (TUIchat-3). `tmux` is intentionally NOT settable as a default
 * — it is reachable only via the per-pane cycle key. Mirrors the wire `default_chat_view_mode`. */
export type DefaultChatViewMode = 'verbose' | 'condensed';

export const DEFAULT_STARTUP_ROGUE_MODELS: Readonly<
  Record<string, readonly StartupRogueModelWire[]>
> = {
  claude_code: [
    { id: 'default', label: 'Default (recommended)' },
    { id: 'sonnet[1m]', label: 'Sonnet (1M context)' },
    { id: 'fable', label: 'Fable' },
    { id: 'opus', label: 'Opus' },
    { id: 'haiku', label: 'Haiku' },
  ],
  codex: [
    { id: 'gpt-5.5', label: 'GPT-5.5' },
    { id: 'gpt-5.4', label: 'GPT-5.4' },
    { id: 'gpt-5.4-mini', label: 'GPT-5.4 Mini' },
    { id: 'gpt-5.3-codex', label: 'GPT-5.3 Codex' },
    { id: 'gpt-5.2', label: 'GPT-5.2' },
  ],
  cursor: [
    { id: 'composer-2.5', label: 'Composer 2.5' },
    { id: 'auto', label: 'Auto' },
    { id: 'gpt-5.5', label: 'GPT-5.5' },
    { id: 'gpt-5.4', label: 'GPT-5.4' },
    { id: 'claude-sonnet-4.5', label: 'Claude Sonnet 4.5' },
  ],
  pi: [
    { id: 'anthropic/claude-sonnet-4-6', label: 'Claude Sonnet 4.6' },
    { id: 'anthropic/claude-opus-4-7', label: 'Claude Opus 4.7' },
    { id: 'openai/gpt-5.5', label: 'GPT-5.5' },
    { id: 'openai/gpt-5.4-mini', label: 'GPT-5.4 Mini' },
  ],
  antigravity: [],
};

export const DEFAULT_STARTUP_ROGUE_EFFORTS: Readonly<Record<string, readonly string[]>> = {
  claude_code: ['low', 'medium', 'high', 'xhigh', 'max'],
  codex: ['low', 'medium', 'high', 'xhigh'],
  cursor: ['slow', 'fast'],
  pi: [],
  antigravity: ['low', 'medium', 'high'],
};

/**
 * The settings slice state. `theme`/`modifier`/`keyOverrides` are the persisted preferences;
 * `status` makes the initial `settings.get` lifecycle explicit (so a view can tell "not loaded yet"
 * from "loaded at defaults"); `error` carries a failed load/save message. All readonly — ref-swapped
 * wholesale on change.
 */
export interface SettingsState {
  /** The selected theme id. Defaults to the hard Everforest Dark build (matches the Python default).
   * This is the source of truth for the *persisted* scheme; the process-global
   * `../../theme/themeStore.ts` mirrors it (what's painted now) for synchronous non-React palette
   * reads, and may transiently diverge from this during a SettingsModal live preview. */
  readonly theme: string;
  /** The command modifier (alt / ctrl / both). Bridged onto the bindings store. */
  readonly modifier: SettingsModifier;
  /** Per-action key-char rebinds, opaque `ActionId -> key char`. Bridged onto the bindings store. */
  readonly keyOverrides: Readonly<Record<string, string>>;
  /** Spaces of horizontal gap between adjacent pane borders (side panes and center panes).
   * `0` = flush borders (the default look); `1`–`4` add spacing. Threaded into the shell's
   * `columnGap`/`rowGap` and the layout manager's inter-region gap (see `App.tsx`/`paneBridge.tsx`). */
  readonly paneGap: number;
  /** Whether vim-style editing is enabled in the chat input. Mirrors the wire `vim_mode`. */
  readonly vimMode: boolean;
  /** The default chat view mode for panes with no per-pane override (TUIchat-3). A pane's effective
   * mode = `conversations.paneViewModes[agentId] ?? defaultChatViewMode`. Only `verbose`/`condensed`
   * are settable here; `tmux` is reachable only via the cycle key. Mirrors the wire
   * `default_chat_view_mode`. */
  readonly defaultChatViewMode: DefaultChatViewMode;
  /** The Startup Rogue auto-spawned on boot, or `null` when none is configured. Mirrors the wire
   * `startup_rogue`. */
  readonly startupRogue: StartupRogueWire | null;
  /** Available Startup Rogue model choices by harness. */
  readonly startupRogueModels: Readonly<Record<string, readonly StartupRogueModelWire[]>>;
  /** Available Startup Rogue effort choices by harness. */
  readonly startupRogueEfforts: Readonly<Record<string, readonly string[]>>;
  /** The user's collaborator-harness override, or `null` when none is set (falls back to
   * `effectiveCollaboratorHarness`). Mirrors the wire `collaborator_harness`. */
  readonly collaboratorHarness: string | null;
  /** The user's planning-agent harness override, or `null` when none is set (falls back to
   * `effectivePlannerHarness`). Mirrors the wire `planner_harness`. */
  readonly plannerHarness: string | null;
  /** The user's crow-harness pool override, or `null` when none is set (falls back to
   * `effectiveCrowHarnesses`). Mirrors the wire `crow_harnesses`. */
  readonly crowHarnesses: readonly string[] | null;
  /** The daemon's live merged collaborator harness (override → role default). Display fallback. */
  readonly effectiveCollaboratorHarness: string;
  /** The daemon's live merged planning-agent harness. Display fallback. */
  readonly effectivePlannerHarness: string;
  /** The daemon's live merged crow-harness pool. Display fallback. */
  readonly effectiveCrowHarnesses: readonly string[];
  /** The LLM provider/tier/role config (api keys masked `***`). Stored in wire shape — its nested
   * keys (`api_key`/`base_url`/`auto_free`) are opaque pass-throughs, not camelCased. `{}` when unset.
   * Built-in `cheap`/`smart` tiers are NOT included here (server-side only); the UI overlays them. */
  readonly llm: LlmWire;
  /** Whether each env-flagged provider's key is present in the daemon's environment. */
  readonly llmEnv: LlmEnvWire;
  /** Load/save lifecycle: `idle` before the first `load`, `ready` after, `error` on a failed RPC. */
  readonly status: 'idle' | 'loading' | 'ready' | 'error';
  /** Set when the last load/save rejected; cleared on the next success. */
  readonly error: string | null;
}

/** The initial, pre-load slice value — the defaults that mirror the Python `TuiUserConfig` defaults
 * (so the UI looks identical before `settings.get` resolves and after, when nothing is persisted). */
export const initialSettingsState: SettingsState = {
  theme: 'everforest-dark',
  modifier: 'alt',
  keyOverrides: {},
  paneGap: 0,
  vimMode: false,
  defaultChatViewMode: 'verbose',
  startupRogue: null,
  startupRogueModels: DEFAULT_STARTUP_ROGUE_MODELS,
  startupRogueEfforts: DEFAULT_STARTUP_ROGUE_EFFORTS,
  collaboratorHarness: null,
  plannerHarness: null,
  crowHarnesses: null,
  effectiveCollaboratorHarness: 'claude_code',
  effectivePlannerHarness: 'claude_code',
  effectiveCrowHarnesses: ['claude_code'],
  llm: {},
  llmEnv: { groq: false, cerebras: false, openrouter: false },
  status: 'idle',
  error: null,
};

/**
 * Slice factory — the trivial Zustand `StateCreator` that seeds the `settings` key. Not a
 * `createListSlice` shell (this slice has its own shape); mutation is the action layer's job
 * (rule 3 — see {@link ./settingsActions.js}). Contributes only the `settings` key; `../store.ts`
 * composes it.
 */
export const createSettingsSlice: StateCreator<
  AppStore,
  [],
  [],
  { settings: SettingsState }
> = () => ({
  settings: initialSettingsState,
});
